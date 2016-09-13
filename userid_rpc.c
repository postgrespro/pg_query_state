#include "postgres.h"

#include "miscadmin.h"
#include "storage/procsignal.h"
#include "storage/shm_mq.h"
#include "utils/timestamp.h"

#define TIMEOUT_MSEC 	1000
#define QUEUE_SIZE		(shm_mq_minimum_size + sizeof(Oid))

/* shared objects */
static shm_mq *mq = NULL;

/* global variables */
static ProcSignalReason UserPollReason;

/*
 * Receive a message from a shared message queue until timeout is exceeded.
 *
 * Parameter `*nbytes` is set to the message length and *data to point to the 
 * message payload. If timeout is exceeded SHM_MQ_WOULD_BLOCK is returned.
 */
static shm_mq_result
shm_mq_receive_with_timeout(shm_mq_handle *mqh, Size *nbytesp, void **datap,
							long timeout)
{

#ifdef HAVE_INT64_TIMESTAMP
#define GetNowFloat()	((float8) GetCurrentTimestamp() / 1000.0)
#else
#define GetNowFloat()	(1000.0 * GetCurrentTimestamp())
#endif

	float8 	endtime = GetNowFloat() + timeout;
	int 	rc = 0;

	for (;;)
	{
		long 		delay;
		shm_mq_result mq_receive_result;

		mq_receive_result = shm_mq_receive(mqh, nbytesp, datap, true);
		if (mq_receive_result != SHM_MQ_WOULD_BLOCK)
			return mq_receive_result;

		if (rc & WL_TIMEOUT)
			return SHM_MQ_WOULD_BLOCK;

		delay = (long) (endtime - GetNowFloat());
		rc = WaitLatch(MyLatch, WL_LATCH_SET | WL_TIMEOUT, delay);
		CHECK_FOR_INTERRUPTS();
		ResetLatch(MyLatch);
	}
}

/*
 * Returns estimated size of shared memory needed for `GetRemoteBackendUserId`.
 */
Size
grbui_EstimateShmemSize()
{
	return QUEUE_SIZE;
}

/*
 * Initialize objects for `GetRemoteBackendUserId` in shared memory.
 *
 * This function must be called inside shared memory initialization block.
 * Flag `initialized` specifies the case when shmem is already initialized.
 */
void
grbui_ShmemInit(void *address, bool initialized)
{
	mq = address;
}

static void
SendCurrentUserId(void)
{
	shm_mq_handle 	*mqh = shm_mq_attach(mq, NULL, NULL);
	Oid 			user_oid = GetUserId();

	shm_mq_send(mqh, sizeof(Oid), &user_oid, false);
}

/*
 * Register `GetRemoteBackendUserId` function as RPC
 */
void
RegisterGetRemoteBackendUserId()
{
	UserPollReason = RegisterCustomProcSignalHandler(SendCurrentUserId);
	if (UserPollReason == INVALID_PROCSIGNAL)
		ereport(WARNING, (errcode(ERRCODE_INSUFFICIENT_RESOURCES),
					errmsg("pg_query_state isn't loaded: insufficient custom ProcSignal slots")));
}

/*
 * Extract effective user id from external backend session specified by `proc`.
 *
 * Assume `proc` is valid backend and doesn't point to current process.
 */
Oid
GetRemoteBackendUserId(PGPROC *proc)
{
	int			sig_result;
	shm_mq_handle	*mqh;
	shm_mq_result 	mq_receive_result;
	Oid			*result;
	Size		res_len;

	Assert(proc && proc != MyProc && proc->backendId != InvalidBackendId);

	mq = shm_mq_create(mq, QUEUE_SIZE);
	shm_mq_set_sender(mq, proc);
	shm_mq_set_receiver(mq, MyProc);

	sig_result = SendProcSignal(proc->pid, UserPollReason, proc->backendId);
	if (sig_result == -1)
		ereport(ERROR, (errcode(ERRCODE_INTERNAL_ERROR),
						errmsg("invalid send signal")));

	mqh = shm_mq_attach(mq, NULL, NULL);
	mq_receive_result = shm_mq_receive_with_timeout(mqh,
													&res_len, 
													(void **) &result,
													TIMEOUT_MSEC);
	if (mq_receive_result != SHM_MQ_SUCCESS)
		ereport(ERROR, (errcode(ERRCODE_INTERNAL_ERROR),
						errmsg("invalid read from message queue")));
	shm_mq_detach(mq);

	return *result;
}
