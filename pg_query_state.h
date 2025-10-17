/*
 * pg_query_state.h
 *		Headers for pg_query_state extension.
 *
 * Copyright (c) 2016-2024, Postgres Professional
 *
 * IDENTIFICATION
 *	  contrib/pg_query_state/pg_query_state.h
 */
#ifndef __PG_QUERY_STATE_H__
#define __PG_QUERY_STATE_H__

#include <postgres.h>

#if PG_VERSION_NUM >= 180000
#include "commands/explain_state.h"
#else
#include "commands/explain.h"
#endif
#include "nodes/pg_list.h"
#include "storage/procarray.h"
#include "storage/shm_mq.h"

#define	QUEUE_SIZE			(16 * 1024)
#define MSG_MAX_SIZE		1024
#define WRITING_DELAY		(100 * 1000) /* 100ms */
#define NUM_OF_ATTEMPTS		6

#define TIMINIG_OFF_WARNING 1
#define BUFFERS_OFF_WARNING 2

#define	PG_QS_MODULE_KEY	0xCA94B108
#define	PG_QS_RCV_KEY       0
#define	PG_QS_SND_KEY       1

/* Receive timeout should be larger than send timeout to let workers stop waiting before polling process */
#define MAX_RCV_TIMEOUT   6000 /* 6 seconds */
#define MAX_SND_TIMEOUT   3000 /* 3 seconds */

/*
 * Delay for receiving parts of full message (in case SHM_MQ_WOULD_BLOCK code),
 * should be tess than MAX_RCV_TIMEOUT
 */
#define PART_RCV_DELAY    1000 /* 1 second */

/*
 * Result status on query state request from asked backend
 */
typedef enum
{
	QUERY_NOT_RUNNING,		/* Backend doesn't execute any query */
	STAT_DISABLED,			/* Collection of execution statistics is disabled */
	QS_RETURNED				/* Backend succx[esfully returned its query state */
} PG_QS_RequestResult;

/*
 *	Format of transmited data through message queue
 */
typedef struct
{
	int     reqid;
	int		length;							/* size of message record, for sanity check */
	PGPROC	*proc;
	PG_QS_RequestResult	result_code;
	int		warnings;						/* bitmap of warnings */
	int		stack_depth;
	char	stack[FLEXIBLE_ARRAY_MEMBER];	/* sequencially laid out stack frames in form of
												text records */
} shm_mq_msg;

#define BASE_SIZEOF_SHM_MQ_MSG (offsetof(shm_mq_msg, stack_depth))

/* pg_query_state arguments */
typedef struct
{
	int     reqid;
	bool 	verbose;
	bool	costs;
	bool	timing;
	bool	buffers;
	bool	triggers;
	ExplainFormat format;
} pg_qs_params;

/* pg_query_state */
extern bool 	pg_qs_enable;
extern bool 	pg_qs_timing;
extern bool 	pg_qs_buffers;
extern List 	*QueryDescStack;
extern pg_qs_params *params;
extern shm_mq 	*mq;

/* signal_handler.c */
extern void SendQueryState(void);
extern void DetachPeer(void);
extern void UnlockShmem(LOCKTAG *tag);
extern void LockShmem(LOCKTAG *tag, uint32 key);

#endif
