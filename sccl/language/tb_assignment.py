# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from dataclasses import dataclass
from enum import Enum
import heapq

from sccl.language.ir import *
from sccl.language.rank_dag import *


def _verify_tb_op_compatible(tb, op):
    s = op.dst.rank if op.is_send() else -1
    r = op.src.rank if op.is_recv() else -1
        
    sends_ok = tb.send == s or s == -1 or tb.send == -1
    recvs_ok = tb.recv == r or r == -1 or tb.recv == -1
    channel_ok = tb.channel == op.channel or tb.channel == -1 or op.channel == -1
    return sends_ok and recvs_ok and channel_ok

# Manual threadblock, channel assignment
def manual_assign_tbs(rank_dag):
    ops = []
    for slot, op in rank_dag.operations.items():
        if op.inst == Instruction.start:
            for o in list(op.next):
                heapq.heappush(ops, o)
        elif op.inst != Instruction.copy:
            heapq.heappush(ops, op)

    visited = set()
    while len(ops) > 0:
        op = heapq.heappop(ops)
        if op not in visited:
            visited.add(op)
            tbid = op.tb
            if tbid not in rank_dag.tbs:
                rank_dag.tbs[tbid] = Threadblock()
            tb = rank_dag.tbs[tbid]
            if _verify_tb_op_compatible(tb, op):
                tb.ops.append(op)
                tb.channel = op.channel
                tb.send = op.dst.rank if op.is_send() else tb.send
                tb.recv = op.src.rank if op.is_recv() else tb.recv
                op.step = len(tb.ops)-1
            else:
                print("Illegal TB assignment")
                print("TODO: Add Debug messages")
                sys.exit()
            
            for o in list(op.next):
                heapq.heappush(ops, o)

    # for tbid, tb in self.tbs.items():
    #     print("TBID", tbid)
    #     for op in tb.ops:
    #         print(op.priority, op.chunk_step, op)


def _get_tb_options(mapping, send, recv, channel, num_tbs, num_channels):
    if send == -1 and recv == -1: # Can go anywhere
        return list(i for i in range(0, num_tbs))
    if channel == -1: # Can go on any channel that matches to send, recv
        options = []
        for ch in range(num_channels):
            if (send, recv, ch) in mapping:
                options.append(mapping[(send, recv, ch)])
        return options
    elif (send, recv, channel) in mapping:
        return [mapping[(send, recv, channel)]]
    else:
        return []

def create_base_tbs(rank_dag):
    ops = []
    tbid = 0
    tb_assignments = {} # (sender, receiver, channel) -> tbid
    num_channels = 1

    for slot, op in rank_dag.operations.items():
        if op.inst == Instruction.start:
            for o in list(op.next):
                ops.append(o)
        elif op.inst != Instruction.copy:
            ops.append(op)

    visited = set()
    while len(ops) > 0:
        op = ops[0]
        if op not in visited:
            visited.add(op)
            s = op.dst.rank if op.is_send() else -1
            r = op.src.rank if op.is_recv() else -1
            channel = 0 if op.channel == -1 else op.channel
            if op.channel >= num_channels:
                num_channels = op.channel + 1

            if (s != -1 or r != -1) and (s,r,channel) not in tb_assignments:
                rank_dag.tbs[tbid] = Threadblock(send=s, recv=r, channel=channel)
                tb_assignments[(s,r,channel)] = tbid
                tbid += 1
        ops = ops[1:] + list(op.next)

    rank_dag.tb_assignments = tb_assignments
    rank_dag.num_channels = num_channels


def auto_assign_tbs(rank_dag):
    # Allocate the base set of TBs
    tb_assignments = rank_dag.tb_assignments
    num_channels = rank_dag.num_channels
    current_num_tb = len(rank_dag.tbs)
    current_tb_step = {}
    for tbid in rank_dag.tbs.keys():
        current_tb_step[tbid] = 0

    ops = []
    for slot, op in rank_dag.operations.items():
        if op.inst == Instruction.start:
            for o in list(op.next):
                ops.append(o)
        elif op.inst != Instruction.copy:
            ops.append(op)
    heapq.heapify(ops)

    visited = set()
    while len(ops) > 0:
        op = heapq.heappop(ops)
        if op not in visited:
            visited.add(op)

            s = op.dst.rank if op.is_send() else -1
            r = op.src.rank if op.is_recv() else -1
            # Get all possible TBs this can be mapped to
            tb_options = _get_tb_options(tb_assignments, s, r, op.channel, current_num_tb, num_channels)
            # If there are multiple options choose the TB at the lowest step
            tbid = tb_options[0]
            if len(tb_options) > 1:
                for tbid_opt in tb_options:
                    if current_tb_step[tbid_opt] < current_tb_step[tbid] and _verify_tb_op_compatible(rank_dag.tbs[tbid], op):
                        tbid = tbid_opt

            tb = rank_dag.tbs[tbid]
            if not _verify_tb_op_compatible(tb, op):
                print(f"Failing: Channel {op.channel}, send {s} recv {r} {op}")
                print("Threadblock", tb.send, tb.recv, tb.channel, tb)
                assert False

            tb.ops.append(op)
            tb.send = op.dst.rank if op.is_send() else tb.send
            tb.recv = op.src.rank if op.is_recv() else tb.recv
            
            op.step = len(tb.ops)-1
            op.channel = tb.channel
            op.tb = tbid
            current_tb_step[tbid] = op.chunk_step

            # For correctness make certain the matching sends and receives
            # happen on the same channel
            for match in op.match:
                match.channel = tb.channel

            for o in list(op.next):
                heapq.heappush(ops, o)

    # for tbid, tb in rank_dag.tbs.items():
    #     print("rank", rank_dag.rank, "TB:", tbid, "s", tb.send, "r", tb.recv)
    #     for op in tb.ops:
    #         print(f"  Chunk step:{op.chunk_step} Chunk priority:{op.priority} {op}")