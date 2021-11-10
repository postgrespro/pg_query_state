/*
 * signal_handler.c
 *		Collect current query state and send it to requestor in custom signal handler
 *
 * Copyright (c) 2016-2016, Postgres Professional
 *
 * IDENTIFICATION
 *	  contrib/pg_query_state/signal_handler.c
 */

#include "pg_query_state.h"

#include "commands/explain.h"
#include "miscadmin.h"
#if PG_VERSION_NUM >= 100000
#include "pgstat.h"
#endif
#include "utils/builtins.h"
#include "utils/memutils.h"

/*
 * Structure of stack frame of fucntion call which resulted from analyze of query state
 */
typedef struct
{
	const char	*query;
	char		*plan;
} stack_frame;

/*
 * An self-explanarory enum describing the send_msg_by_parts results
 */
typedef enum
{
	MSG_BY_PARTS_SUCCEEDED,
	MSG_BY_PARTS_FAILED
} msg_by_parts_result;

static msg_by_parts_result send_msg_by_parts(shm_mq_handle *mqh, Size nbytes, const void *data);

/*
 *	Get List of stack_frames as a stack of function calls starting from outermost call.
 *		Each entry contains query text and query state in form of EXPLAIN ANALYZE output.
 *	Assume extension is enabled and QueryDescStack is not empty
 */
static List *
runtime_explain()
{
	ExplainState    *es;
	ListCell	    *i;
	List			*result = NIL;

	Assert(list_length(QueryDescStack) > 0);

	/* initialize explain state with all config parameters */
	es = NewExplainState();
	es->analyze = true;
	es->verbose = params->verbose;
	es->costs = params->costs;
	es->buffers = params->buffers && pg_qs_buffers;
	es->timing = params->timing && pg_qs_timing;
	es->summary = false;
	es->format = params->format;
	es->runtime = true;

	/* collect query state outputs of each plan entry of stack */
	foreach(i, QueryDescStack)
	{
		QueryDesc 	*currentQueryDesc = (QueryDesc *) lfirst(i);
		stack_frame	*qs_frame = palloc(sizeof(stack_frame));

		/* save query text */
		qs_frame->query = currentQueryDesc->sourceText;

		/* save plan with statistics */
		initStringInfo(es->str);
		ExplainBeginOutput(es);
		ExplainPrintPlan(es, currentQueryDesc);
		if (params->triggers)
			ExplainPrintTriggers(es, currentQueryDesc);
		ExplainEndOutput(es);

		/* Remove last line break */
		if (es->str->len > 0 && es->str->data[es->str->len - 1] == '\n')
			es->str->data[--es->str->len] = '\0';

		/* Fix JSON to output an object */
		if (params->format == EXPLAIN_FORMAT_JSON)
		{
			es->str->data[0] = '{';
			es->str->data[es->str->len - 1] = '}';
		}

		qs_frame->plan = es->str->data;

		result = lcons(qs_frame, result);
	}

	return result;
}

/*
 * Compute length of serialized stack frame
 */
static int
serialized_stack_frame_length(stack_frame *qs_frame)
{
	return 	INTALIGN(strlen(qs_frame->query) + VARHDRSZ)
		+ 	INTALIGN(strlen(qs_frame->plan) + VARHDRSZ);
}

/*
 * Compute overall length of serialized stack of function calls
 */
static int
serialized_stack_length(List *qs_stack)
{
	ListCell 	*i;
	int			result = 0;

	foreach(i, qs_stack)
	{
		stack_frame *qs_frame = (stack_frame *) lfirst(i);

		result += serialized_stack_frame_length(qs_frame);
	}

	return result;
}

/*
 * Convert stack_frame record into serialized text format version
 * 		Increment '*dest' pointer to the next serialized stack frame
 */
static void
serialize_stack_frame(char **dest, stack_frame *qs_frame)
{
	SET_VARSIZE(*dest, strlen(qs_frame->query) + VARHDRSZ);
	memcpy(VARDATA(*dest), qs_frame->query, strlen(qs_frame->query));
	*dest += INTALIGN(VARSIZE(*dest));

	SET_VARSIZE(*dest, strlen(qs_frame->plan) + VARHDRSZ);
	memcpy(VARDATA(*dest), qs_frame->plan, strlen(qs_frame->plan));
	*dest += INTALIGN(VARSIZE(*dest));
}

/*
 * Convert List of stack_frame records into serialized structures laid out sequentially
 */
static void
serialize_stack(char *dest, List *qs_stack)
{
	ListCell		*i;

	foreach(i, qs_stack)
	{
		stack_frame *qs_frame = (stack_frame *) lfirst(i);

		serialize_stack_frame(&dest, qs_frame);
	}
}

static msg_by_parts_result
shm_mq_send_nonblocking(shm_mq_handle *mqh, Size nbytes, const void *data, Size attempts)
{
	int				i;
	shm_mq_result	res;

	for(i = 0; i < attempts; i++)
	{
#if PG_VERSION_NUM < 150000
		res = shm_mq_send(mqh, nbytes, data, true);
#else
		res = shm_mq_send(mqh, nbytes, data, true, true);
#endif

		if(res == SHM_MQ_SUCCESS)
			break;
		else if (res == SHM_MQ_DETACHED)
			return MSG_BY_PARTS_FAILED;

		/* SHM_MQ_WOULD_BLOCK - sleeping for some delay */
		pg_usleep(WRITING_DELAY);
	}

	if(i == attempts)
		return MSG_BY_PARTS_FAILED;

	return MSG_BY_PARTS_SUCCEEDED;
}

/*
 * send_msg_by_parts sends data through the queue as a bunch of messages
 * of smaller size
 */
static msg_by_parts_result
send_msg_by_parts(shm_mq_handle *mqh, Size nbytes, const void *data)
{
	int bytes_left;
	int bytes_send;
	int offset;

	/* Send the expected message length */
	if(shm_mq_send_nonblocking(mqh, sizeof(Size), &nbytes, NUM_OF_ATTEMPTS) == MSG_BY_PARTS_FAILED)
		return MSG_BY_PARTS_FAILED;

	/* Send the message itself */
	for (offset = 0; offset < nbytes; offset += bytes_send)
	{
		bytes_left = nbytes - offset;
		bytes_send = (bytes_left < MSG_MAX_SIZE) ? bytes_left : MSG_MAX_SIZE;
		if(shm_mq_send_nonblocking(mqh, bytes_send, &(((unsigned char*)data)[offset]), NUM_OF_ATTEMPTS)
			== MSG_BY_PARTS_FAILED)
			return MSG_BY_PARTS_FAILED;
	}

	return MSG_BY_PARTS_SUCCEEDED;
}

/*
 * Send state of current query to shared queue.
 * This function is called when fire custom signal QueryStatePollReason
 */
void
SendQueryState(void)
{
	shm_mq_handle 	*mqh;
	instr_time	start_time;
	instr_time	cur_time;
	int64 		delay = MAX_SND_TIMEOUT;
	int         reqid = params->reqid;
	LOCKTAG		tag;

	INSTR_TIME_SET_CURRENT(start_time);

	/* wait until caller sets this process as sender to message queue */
	for (;;)
	{
		if (shm_mq_get_sender(mq) == MyProc)
			break;

#if PG_VERSION_NUM < 100000
		WaitLatch(MyLatch, WL_LATCH_SET | WL_TIMEOUT, delay);
#elif PG_VERSION_NUM < 120000
		WaitLatch(MyLatch, WL_LATCH_SET | WL_TIMEOUT, delay, PG_WAIT_IPC);
#else
		WaitLatch(MyLatch, WL_LATCH_SET | WL_EXIT_ON_PM_DEATH | WL_TIMEOUT, delay, PG_WAIT_IPC);
#endif
		INSTR_TIME_SET_CURRENT(cur_time);
		INSTR_TIME_SUBTRACT(cur_time, start_time);

		delay = MAX_SND_TIMEOUT - (int64) INSTR_TIME_GET_MILLISEC(cur_time);
		if (delay <= 0)
		{
			elog(WARNING, "pg_query_state: failed to receive request from leader");
			DetachPeer();
			return;
		}
		CHECK_FOR_INTERRUPTS();
		ResetLatch(MyLatch);
	}

	LockShmem(&tag, PG_QS_SND_KEY);

	elog(DEBUG1, "Worker %d receives pg_query_state request from %d", shm_mq_get_sender(mq)->pid, shm_mq_get_receiver(mq)->pid);
	mqh = shm_mq_attach(mq, NULL, NULL);

	if (reqid != params->reqid || shm_mq_get_sender(mq) != MyProc)
	{
		UnlockShmem(&tag);
		return;
	}
	/* check if module is enabled */
	if (!pg_qs_enable)
	{
		shm_mq_msg msg = { reqid, BASE_SIZEOF_SHM_MQ_MSG, MyProc, STAT_DISABLED };

		if(send_msg_by_parts(mqh, msg.length, &msg) != MSG_BY_PARTS_SUCCEEDED)
			goto connection_cleanup;
	}

	/* check if backend doesn't execute any query */
	else if (list_length(QueryDescStack) == 0)
	{
		shm_mq_msg msg = { reqid, BASE_SIZEOF_SHM_MQ_MSG, MyProc, QUERY_NOT_RUNNING };

		if(send_msg_by_parts(mqh, msg.length, &msg) != MSG_BY_PARTS_SUCCEEDED)
			goto connection_cleanup;
	}

	/* happy path */
	else
	{
		List			*qs_stack = runtime_explain();
		int				msglen = sizeof(shm_mq_msg) + serialized_stack_length(qs_stack);
		shm_mq_msg		*msg = palloc(msglen);

		msg->reqid = reqid;
		msg->length = msglen;
		msg->proc = MyProc;
		msg->result_code = QS_RETURNED;

		msg->warnings = 0;
		if (params->timing && !pg_qs_timing)
			msg->warnings |= TIMINIG_OFF_WARNING;
		if (params->buffers && !pg_qs_buffers)
			msg->warnings |= BUFFERS_OFF_WARNING;

		msg->stack_depth = list_length(qs_stack);
		serialize_stack(msg->stack, qs_stack);

		if(send_msg_by_parts(mqh, msglen, msg) != MSG_BY_PARTS_SUCCEEDED)
		{
			elog(WARNING, "pg_query_state: peer seems to have detached");
			goto connection_cleanup;
		}
	}
	elog(DEBUG1, "Worker %d sends response for pg_query_state to %d", shm_mq_get_sender(mq)->pid, shm_mq_get_receiver(mq)->pid);
	DetachPeer();
	UnlockShmem(&tag);

	return;

connection_cleanup:
#if PG_VERSION_NUM < 100000
	shm_mq_detach(mq);
#else
	shm_mq_detach(mqh);
#endif
	DetachPeer();
	UnlockShmem(&tag);
}
