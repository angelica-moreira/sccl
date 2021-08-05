# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.


from dataclasses import dataclass
from enum import Enum
from sccl.language.ir import *

_current_program = None
def _curr():
    global _current_program
    if _current_program == None:
        raise RuntimeError("No Program in context")
    return _current_program

class SCCLProgram:
    def __init__(self, name, collective, topo):
        self.name = name
        self.collective = collective
        self.topo = topo
        # Initialize the chunks on each rank according to the precondition
        self.ranks = []
        for r in collective.ranks():
            input_chunks = [None] * collective.num_chunks
            output_chunks = [None] * collective.num_chunks
            scratch_chunks = [None] * collective.num_chunks
            for c in collective.chunks():
                if collective.precondition(r, c):
                    input_chunks[c] = Ref(Buffer.input, c, 1, self, r)
            chunks = {Buffer.input : input_chunks, 
                      Buffer.output : output_chunks, 
                      Buffer.scratch : scratch_chunks}
            self.ranks.append(Process(self, r, chunks))

    def rank(self, rank):
        return self.ranks[rank]

    # Checks that all chunks that should be on each rank
    # are present. Does not check if they are ordered correctly.
    def check(self):
        correct = True
        for r in self.collective.ranks():
            output_chunks = self.ranks[r].chunks[Buffer.output]
            for c in self.collective.chunks():
                if self.collective.postcondition(r, c) and output_chunks[c] is None:
                    print(f'Rank {r} chunk {c} is missing')
                    correct = False
        return correct

    def lower(self):
        gpu_prgms = [rank.lower() for rank in self.ranks]
        return Program(self.name, gpu_prgms)

    def __enter__(self):
        global _current_program
        if _current_program != None:
            raise RuntimeError("There is already a SCCL Program in context")
        _current_program = self
    
    def __exit__(self, exc_type, exc_value, exc_traceback):
        global _current_program
        if _current_program != self:
            raise RuntimeError("This program is not currently in context")
        _current_program = None

def Rank(index):
    return _curr().rank(index)

def XML():
   print(ir_to_xml(_curr().lower()))

def Check():
    return _curr().check()


class Process:
    def __init__(self, prog, rank, chunks):
        self.prog = prog
        self.rank = rank
        self.chunks = chunks
        self.tbs = {}
    
    def input(self, id):
        # TODO: mark input
        # return Ref(self.prog, self, id)
        return self.chunks[Buffer.input][id]

    def _add_send(self, tbid, step, ch, op):
        # print(f'Send {op.dst.index} from {op.src.rank} to {op.dst.rank} {tbid} {step}')
        assert(op.inst == Instruction.send)
        sendto = op.dst.rank
        if tbid not in self.tbs:
            self.tbs[tbid] = Threadblock(ch, send=sendto, ops={step: op})
        else:
            tb = self.tbs[tbid]
            assert (tb.send == -1 or tb.send == sendto), \
                f'Rank {self.rank}: Threadblock {tbid} is already set to send to {tb.send}, trying to send to {sendto}'
            tb.send = sendto
            assert step not in tb.ops, f'Step {step} already in rank {self.rank} tbid {tbid}'
            tb.ops[step] = op

    def _add_recv(self, tbid, step, ch, op):
        assert(op.inst == Instruction.recv)
        recvd_chunk = op.dst
        self.chunks[recvd_chunk.buffer][recvd_chunk.index] = recvd_chunk
        # print(f"{self.rank} adds chunk to index {recvd_chunk.index}")
        receivefrom = op.src.rank
        if tbid not in self.tbs:
            self.tbs[tbid] = Threadblock(ch, recv=receivefrom, ops={step: op})
        else:
            tb = self.tbs[tbid]
            assert (tb.recv == -1 or tb.recv == receivefrom), \
                   f'Rank {self.rank}: Threadblock {tbid} is already set to receive from {tb.recv}, trying to receive from {receivefrom}'

            tb.recv = receivefrom
            assert step not in tb.ops, f'Step {step} in rank {self.rank} tbid {tbid}'
            tb.ops[step] = op

    def _add_copy(self, tbid, step, ch, op):
        assert(op.inst == Instruction.copy)
        self.chunks[op.dst.buffer][op.dst.index] = op.dst
        if tbid not in self.tbs:
            self.tbs[tbid] = Threadblock(ch, ops={step: op})
        else:
            tb = self.tbs[tbid]
            tb.ops[step] = op

    def lower(self):
        for tb in self.tbs.values():
            tb.ops = [v for k,v in sorted(tb.ops.items())]
        return Gpu(self.rank, self.tbs.values())

@dataclass
class Ref(ChunkRef):
    prog: SCCLProgram
    rank: int
    hops: int = 0 
    creator: Op = None

    def _get_ref(self, dst, buffer, index, size):
        index = self.index if index == -1 else index
        size = self.size if size == -1 else size
        return Ref(buffer, index, self.size, self.prog, dst, self.hops+1, self)

    def send(self, dst, size=-1, step=-1, sendtb=-1, recvtb=-1, ch=0, buffer=Buffer.output, index=-1):
        sendtb = dst if sendtb == -1 else sendtb
        recvtb = self.rank if recvtb == -1 else recvtb
        dstchunk = self._get_ref(dst, buffer, index, size)
        depends = [] if self.creator is None else [self.creator]
        self.prog.ranks[self.rank]._add_send(sendtb, step, ch, Op(Instruction.send, self, dstchunk, depends))
        receiveInstr = Op(Instruction.recv, self, dstchunk, [])
        self.prog.ranks[dst]._add_recv(recvtb, step, ch, receiveInstr)
        dstchunk.creator = receiveInstr
        return dstchunk
    
    def wait(self, steps):
        # TODO: fix this - I don't think we need this anymore?
        future = Ref(self.prog, self.proc, )
        self.prog._add_op(TransferOp(OpCode.Wait, self, future))
        return future

    def copyto(self, buffer=Buffer.output, size=-1, index=-1, step=-1, tb=-1, ch=0):
        dstchunk = self._get_ref(self.rank, buffer, index, size)
        self.prog.ranks[self.rank]._add_copy(tb, step, ch, Op(Instruction.copy, self, dstchunk, []))
        return dstchunk

    def reduce(self, other):
        # TODO: do something
        return self