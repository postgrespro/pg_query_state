/*
 * pg_query_state.h
 *		Headers for pg_query_state extension.
 *
 * Copyright (c) 2016-2016, Postgres Professional
 *
 * IDENTIFICATION
 *	  contrib/pg_query_state/pg_query_state.h
 */
#ifndef __PG_QUERY_STATE_H__
#define __PG_QUERY_STATE_H__

#include <postgres.h>

#include "commands/explain.h"
#include "nodes/pg_list.h"
#include "storage/shm_mq.h"

#define	QUEUE_SIZE			(16 * 1024)

#define TIMINIG_OFF_WARNING 1
#define BUFFERS_OFF_WARNING 2

/*
 * Result status on query state request from asked backend
 */
typedef enum
{
	QUERY_NOT_RUNNING,		/* Backend doesn't execute any query */
	STAT_DISABLED,			/* Collection of execution statistics is disabled */
	QS_RETURNED				/* Backend succesfully returned its query state */
} PG_QS_RequestResult;

/*
 *	Format of transmited data through message queue
 */
typedef struct
{
	int		length;							/* size of message record, for sanity check */
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

#endif
