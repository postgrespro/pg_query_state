#include "postgres.h"

#include "miscadmin.h"
#include "storage/procsignal.h"
#include "storage/shm_mq.h"
#include "utils/timestamp.h"

#define TIMEOUT_MSEC 	1000
#define QUEUE_SIZE		(shm_mq_minimum_size + sizeof(Oid))

/* shared objects */
typedef struct
{
	LWLock 	*lock;
	Oid		 userid;
} uirpcFuncResult;
static uirpcFuncResult *resptr = NULL;

/* global variables */
static ProcSignalReason UserPollReason = INVALID_PROCSIGNAL;

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
uirpcEstimateShmemSize()
{
	return MAXALIGN(sizeof(uirpcFuncResult));
}

/*
 * Initialize objects for `GetRemoteBackendUserId` in shared memory.
 *
 * This function must be called inside shared memory initialization block.
 * Flag `initialized` specifies the case when shmem is already initialized.
 */
void
uirpcShmemInit()
{
	bool found;

	resptr = ShmemInitStruct("userid_rpc",
							sizeof(uirpcFuncResult),
							&found);
	if (!found)
		/* First time through ... */
		resptr->lock = &(GetNamedLWLockTranche("userid_rpc"))->lock;
	resptr->userid = InvalidOid;
}

static void
SendCurrentUserId(void)
{
	LWLockAcquire(resptr->lock, LW_EXCLUSIVE);
	LWLockUpdateVar(resptr->lock, (uint64 *) &resptr->userid, GetUserId());
	LWLockRelease(resptr->lock);
}

/*
 * Register `GetRemoteBackendUserId` function as RPC and request necessary
 * 		locks.
 */
void
RegisterGetRemoteBackendUserId()
{
	UserPollReason = RegisterCustomProcSignalHandler(SendCurrentUserId);
	if (UserPollReason == INVALID_PROCSIGNAL)
		ereport(WARNING, (errcode(ERRCODE_INSUFFICIENT_RESOURCES),
					errmsg("pg_query_state isn't loaded: insufficient custom ProcSignal slots")));

	RequestNamedLWLockTranche("userid_rpc", 1);
}

/*
 * Extract effective user id from external backend session specified by `proc`.
 *
 * This function must be called after `UserPollReason` register and
 * initialization specialized structures in shared memory.
 * Assume `proc` is valid backend and doesn't point to current process.
 */
Oid
GetRemoteBackendUserId(PGPROC *proc)
{
	int	sig_result;
	Oid	result = InvalidOid;

	Assert(UserPollReason != INVALID_PROCSIGNAL);
	Assert(resptr != NULL);
	Assert(proc && proc != MyProc && proc->backendId != InvalidBackendId);

	sig_result = SendProcSignal(proc->pid, UserPollReason, proc->backendId);
	if (sig_result == -1)
		ereport(ERROR, (errcode(ERRCODE_INTERNAL_ERROR),
						errmsg("invalid send signal")));

	while (result == InvalidOid)
		LWLockWaitForVar(resptr->lock,
						 (uint64 *) &resptr->userid,
						 result,
						 (uint64 *) &result);

	return result;
}
