/* SPDX-License-Identifier: GPL-2.0 */
#define _GNU_SOURCE

#include <errno.h>
#include <linux/io_uring.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>

#ifndef MAP_POPULATE
#define MAP_POPULATE 0
#endif

struct ring_mapping {
	void *sq_ring;
	void *cq_ring;
	struct io_uring_sqe *sqes;
	size_t sq_ring_size;
	size_t cq_ring_size;
	size_t sqes_size;
};

static int fail(const char *operation)
{
	fprintf(stderr, "%s: %s\n", operation, strerror(errno));
	return -1;
}

static int io_uring_setup_raw(unsigned entries, struct io_uring_params *params)
{
	return (int)syscall(__NR_io_uring_setup, entries, params);
}

static int io_uring_enter_raw(int fd, unsigned submit, unsigned complete,
			      unsigned flags)
{
	return (int)syscall(__NR_io_uring_enter, fd, submit, complete, flags,
			    NULL, 0);
}

static int io_uring_register_raw(int fd, unsigned opcode, const void *arg,
				 unsigned count)
{
	return (int)syscall(__NR_io_uring_register, fd, opcode, arg, count);
}

static void unmap_ring(struct ring_mapping *mapping, int single_mapping)
{
	if (mapping->sqes != MAP_FAILED)
		munmap(mapping->sqes, mapping->sqes_size);
	if (!single_mapping && mapping->cq_ring != MAP_FAILED)
		munmap(mapping->cq_ring, mapping->cq_ring_size);
	if (mapping->sq_ring != MAP_FAILED)
		munmap(mapping->sq_ring, mapping->sq_ring_size);
}

static int map_ring(int fd, const struct io_uring_params *params,
		    struct ring_mapping *mapping)
{
	int single_mapping = params->features & IORING_FEAT_SINGLE_MMAP;
	size_t shared_size;

	mapping->sq_ring = MAP_FAILED;
	mapping->cq_ring = MAP_FAILED;
	mapping->sqes = MAP_FAILED;
	mapping->sq_ring_size = params->sq_off.array +
		params->sq_entries * sizeof(uint32_t);
	mapping->cq_ring_size = params->cq_off.cqes +
		params->cq_entries * sizeof(struct io_uring_cqe);
	mapping->sqes_size = params->sq_entries * sizeof(struct io_uring_sqe);
	shared_size = mapping->sq_ring_size > mapping->cq_ring_size ?
		mapping->sq_ring_size : mapping->cq_ring_size;
	if (single_mapping)
		mapping->sq_ring_size = shared_size;

	mapping->sq_ring = mmap(NULL, mapping->sq_ring_size,
				PROT_READ | PROT_WRITE,
				MAP_SHARED | MAP_POPULATE, fd,
				IORING_OFF_SQ_RING);
	if (mapping->sq_ring == MAP_FAILED)
		return fail("mmap(SQ ring)");
	if (single_mapping) {
		mapping->cq_ring = mapping->sq_ring;
	} else {
		mapping->cq_ring = mmap(NULL, mapping->cq_ring_size,
					PROT_READ | PROT_WRITE,
					MAP_SHARED | MAP_POPULATE, fd,
					IORING_OFF_CQ_RING);
		if (mapping->cq_ring == MAP_FAILED) {
			unmap_ring(mapping, single_mapping);
			return fail("mmap(CQ ring)");
		}
	}
	mapping->sqes = mmap(NULL, mapping->sqes_size,
			      PROT_READ | PROT_WRITE,
			      MAP_SHARED | MAP_POPULATE, fd,
			      IORING_OFF_SQES);
	if (mapping->sqes == MAP_FAILED) {
		unmap_ring(mapping, single_mapping);
		return fail("mmap(SQEs)");
	}
	return 0;
}

static int test_register_probe(int fd)
{
	const unsigned operation_count = 256;
	size_t size = sizeof(struct io_uring_probe) +
		operation_count * sizeof(struct io_uring_probe_op);
	struct io_uring_probe *probe = calloc(1, size);
	int result = -1;

	if (!probe)
		return fail("calloc(probe)");
	if (io_uring_register_raw(fd, IORING_REGISTER_PROBE, probe,
				  operation_count) < 0) {
		fail("io_uring_register(PROBE)");
		goto out;
	}
	if (!probe->ops_len) {
		errno = EIO;
		fail("io_uring_register(PROBE empty)");
		goto out;
	}
	puts("IO_URING_REGISTER_OK");
	result = 0;
out:
	free(probe);
	return result;
}

static int submit_nop(int fd, const struct io_uring_params *params,
		      const struct ring_mapping *mapping)
{
	volatile uint32_t *sq_head = mapping->sq_ring + params->sq_off.head;
	volatile uint32_t *sq_tail = mapping->sq_ring + params->sq_off.tail;
	volatile uint32_t *sq_mask = mapping->sq_ring + params->sq_off.ring_mask;
	uint32_t *sq_array = mapping->sq_ring + params->sq_off.array;
	volatile uint32_t *cq_head = mapping->cq_ring + params->cq_off.head;
	volatile uint32_t *cq_tail = mapping->cq_ring + params->cq_off.tail;
	volatile uint32_t *cq_mask = mapping->cq_ring + params->cq_off.ring_mask;
	struct io_uring_cqe *cqes = mapping->cq_ring + params->cq_off.cqes;
	const uint64_t user_data = UINT64_C(0x4c4d494f5552494e);
	uint32_t tail = __atomic_load_n(sq_tail, __ATOMIC_RELAXED);
	uint32_t index = tail & __atomic_load_n(sq_mask, __ATOMIC_RELAXED);
	uint32_t completion_head;
	struct io_uring_cqe *completion;

	if (tail - __atomic_load_n(sq_head, __ATOMIC_ACQUIRE) >=
	    params->sq_entries) {
		errno = EBUSY;
		return fail("SQ ring full");
	}
	memset(&mapping->sqes[index], 0, sizeof(mapping->sqes[index]));
	mapping->sqes[index].opcode = IORING_OP_NOP;
	mapping->sqes[index].user_data = user_data;
	sq_array[index] = index;
	__atomic_store_n(sq_tail, tail + 1, __ATOMIC_RELEASE);

	if (io_uring_enter_raw(fd, 1, 1, IORING_ENTER_GETEVENTS) < 0)
		return fail("io_uring_enter(NOP)");
	completion_head = __atomic_load_n(cq_head, __ATOMIC_RELAXED);
	if (completion_head == __atomic_load_n(cq_tail, __ATOMIC_ACQUIRE)) {
		errno = EIO;
		return fail("CQ ring empty");
	}
	completion = &cqes[
		completion_head & __atomic_load_n(cq_mask, __ATOMIC_RELAXED)
	];
	if (completion->user_data != user_data || completion->res != 0) {
		errno = EIO;
		return fail("unexpected NOP completion");
	}
	__atomic_store_n(cq_head, completion_head + 1, __ATOMIC_RELEASE);
	puts("IO_URING_ENTER_OK");
	return 0;
}

int main(void)
{
	struct io_uring_params params;
	struct ring_mapping mapping;
	int single_mapping;
	int fd;
	int result = 1;

	memset(&params, 0, sizeof(params));
	fd = io_uring_setup_raw(8, &params);
	if (fd < 0)
		return fail("io_uring_setup");
	puts("IO_URING_SETUP_OK");
	if (map_ring(fd, &params, &mapping) != 0)
		goto out_close;
	single_mapping = params.features & IORING_FEAT_SINGLE_MMAP;
	if (test_register_probe(fd) != 0 ||
	    submit_nop(fd, &params, &mapping) != 0)
		goto out_unmap;
	puts("RESULT_OK");
	result = 0;
out_unmap:
	unmap_ring(&mapping, single_mapping);
out_close:
	if (close(fd) != 0 && result == 0)
		result = fail("close(io_uring)");
	return result;
}
