"""PagedAttention Virtual Memory Block Allocator for LLM KV-Cache.

Manages discrete physical blocks of GPU VRAM across variable sequence lengths.
Eliminates internal and external fragmentation by dynamic block allocation.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PhysicalBlock:
    block_id: int
    block_size: int = 16
    ref_count: int = 0
    data: list[int] = field(default_factory=list)


class PagedKVCacheManager:
    """Allocates and frees fixed-size KV-cache blocks with prefix sharing support."""

    def __init__(self, num_blocks: int = 1024, block_size: int = 16) -> None:
        self.block_size = block_size
        self.num_blocks = num_blocks
        self.free_blocks: list[int] = list(range(num_blocks))
        self.block_table: dict[str, list[int]] = {}
        self.physical_blocks: dict[int, PhysicalBlock] = {
            i: PhysicalBlock(block_id=i, block_size=block_size) for i in range(num_blocks)
        }

    def allocate_sequence(self, seq_id: str, prompt_len: int) -> list[int]:
        """Allocates required physical blocks for an incoming sequence."""
        needed_blocks = (prompt_len + self.block_size - 1) // self.block_size
        if len(self.free_blocks) < needed_blocks:
            raise MemoryError(f"Out of GPU VRAM blocks: needed {needed_blocks}, free {len(self.free_blocks)}")

        allocated = [self.free_blocks.pop(0) for _ in range(needed_blocks)]
        for bid in allocated:
            self.physical_blocks[bid].ref_count += 1

        self.block_table[seq_id] = allocated
        return allocated

    def free_sequence(self, seq_id: str) -> None:
        """Deallocates sequence blocks and returns them to the free pool."""
        if seq_id not in self.block_table:
            return

        for bid in self.block_table[seq_id]:
            self.physical_blocks[bid].ref_count -= 1
            if self.physical_blocks[bid].ref_count == 0:
                self.physical_blocks[bid].data.clear()
                self.free_blocks.append(bid)

        del self.block_table[seq_id]
