# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import argparse

from sccl.language import *
from sccl.topologies.distributed import *
from sccl.topologies import *
from sccl.language.collectives import Collective

class Pipeline(Collective):
    def init_buffers(self):
        chunks_per_node = self.instances
        rank_buffers = []
        for r in range(self.num_ranks):
            input_buffer = [None] * chunks_per_node
            output_buffer = [None] * chunks_per_node
            if r != self.num_ranks -1:
                for c in range(chunks_per_node):
                    input_buffer[c] = Chunk(r, c, r+1, c)
            buffers = {Buffer.input : input_buffer, 
                       Buffer.output : output_buffer}
            rank_buffers.append(buffers)
        return rank_buffers
            

    # Final state chunks on rank(i) end up on rank(i+1)
    def check(self, prog):
        correct = True
        for r in range(1, self.num_ranks):
            output = prog.ranks[r].buffers[Buffer.output]
            for c in range(self.instances):
                chunk = output[c]
                if chunk is None or chunk.origin_rank != r-1 or chunk.origin_index != c:
                    print(f'Rank {r} chunk {c} is incorrect should be ({r-1}, {c}) given {chunk}')
                    correct = False
        return correct


def pipeline(num_nodes, instances):
    local_topology = dgx1()
    num_local_gpus = 8
    chunks = num_local_gpus
    total_chunks_per_loop = chunks * instances
    remote_bw = 1
    size = num_local_gpus * num_nodes
    topology = fully_connected(size)
    collective = Pipeline(size, total_chunks_per_loop, True, "custom")

    def rank(node, local_rank):
        return node * num_local_gpus + local_rank
    
    with SCCLProgram("pipeline", topology, collective, total_chunks_per_loop):

        # Allocate scratch space
        for n in range(num_nodes):
            for g in range(num_local_gpus):
                r1 = rank(n, g)
                if n < num_nodes: # Gather scratch
                    Rank(r1).create_scratch('gather') 
                if n > 0: # Scatter scratch
                    Rank(r1).create_scratch('scatter') 

        for i in range(instances):
            for n in range(num_nodes):
                for g in range(num_local_gpus):
                    r = rank(n, g)

                    # Do nothing for last gpu - end of pipeline
                    if r == size - 1:
                        continue

                    # Cross node send
                    if g == num_local_gpus -1:
                        for ch in range(chunks):
                            c = Rank(r).input(ch*instances + i)
                            if ch == 0:
                                c = c.send(rank(n, ch), 'gather', i, ch=ch%2+i*2)

                            elif ch == num_local_gpus-1:
                                c = c.send(rank(n+1, ch), 'scatter', i, ch=ch%2+i*2)
                            else:
                                c = c.send(rank(n, ch), 'gather', i, ch=ch%2+i*2)
                                c = c.send(rank(n+1, ch), 'scatter', i, ch=ch%2+i*2)
                            
                            c.send(r+1, Buffer.output, c.get_dst_index(), ch=ch%2+i*2)
                            
                    # Normal send
                    else:
                        c = Rank(r).input(i * chunks, chunks)
                        c.send(r+1, Buffer.output, i * chunks, ch=g%2+i*2)
        
        Check()
        XML()
parser = argparse.ArgumentParser()
parser.add_argument('num_nodes', type=int, help ='number of nodes')
parser.add_argument('instances', type=int, help ='number of instances')

args = parser.parse_args()

pipeline(args.num_nodes, args.instances)