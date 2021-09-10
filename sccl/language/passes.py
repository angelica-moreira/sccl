# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from sccl.language.ir import *

# Given the set of operations that operate over a particular slot (rank, buffer, idx) fixed
# Try and replace operations with pipelined ops like receive copy send (rcs)
# or receive reduce send (rrs) and receive reduce copy send (rrcs)
# TODO: Only works if there are no multi chunk sends!!!!!!
# Rules:
# recv-copy-send 
# recv(src, sbuf, si, _, _, _ ) send(_, _, _, dst, dbuf, di) -> recv_copy_send(src, sbuf, si, dst, dbuf, di)
def rcs(ops, tbs):
    delete_idx = []
    if len(ops) >= 2:
        for i in range(0, len(ops)-1):
            if ops[i].inst == Instruction.recv and ops[i+1].inst == Instruction.send and ops[i].tb == ops[i+1].tb:
                new_op = Op(Instruction.recv_copy_send, ops[i].src, ops[i+1].dst, ops[i].depends + ops[i+1].depends, ops[i].step, ops[i].tb)
                ops[i] = new_op
                # Temporary until we automate tb assignment
                tb = tbs[new_op.tb]
                tb.ops[new_op.step] = new_op
                old_op = ops[i+1]
                del tb.ops[old_op.step]

                delete_idx.append(i+1)
    
    delete_idx.sort(reverse=True)
    # Delete the ops
    for i in delete_idx:
        del ops[i]

    # Update the depends of ops - always depend on the ops ahead 
    for i in range(1, len(ops)):
        ops[i].depends = [ops[i-1]]


def rrcs_rrs(ops, tbs):
    delete_idx = []
    if len(ops) >= 3:
        for i in range(0, len(ops)-2):
            if ops[i].inst == Instruction.recv_reduce_copy and ops[i+1].inst == Instruction.send and ops[i].tb == ops[i+1].tb and ops[i+2].inst == Instruction.recv:
                new_op = Op(Instruction.recv_reduce_send, ops[i].src, ops[i+1].dst, ops[i].depends, ops[i].step, ops[i].tb)
                ops[i] = new_op
                # Temporary until we automate tb assignment
                tb = tbs[new_op.tb]
                tb.ops[new_op.step] = new_op
                old_op = ops[i+1]
                del tb.ops[old_op.step]

                delete_idx.append(i+1)

    if len(ops) >= 2:
        for i in range(0, len(ops)-1):
            if ops[i].inst == Instruction.recv_reduce_copy and ops[i+1].inst == Instruction.send and ops[i].tb == ops[i+1].tb:
                new_op = Op(Instruction.recv_reduce_copy_send, ops[i].src, ops[i+1].dst, ops[i].depends, ops[i].step, ops[i].tb)
                ops[i] = new_op
                
                # Temporary until we automate tb assignment
                tb = tbs[new_op.tb]
                tb.ops[new_op.step] = new_op
                old_op = ops[i+1]
                del tb.ops[old_op.step]
                
                delete_idx.append(i+1)
    
    delete_idx.sort(reverse=True)
    # Delete the ops
    for i in delete_idx:
        del ops[i]

    # Update the depends of ops - always depend on the ops ahead 
    for i in range(1, len(ops)):
        ops[i].depends = [ops[i-1]]


