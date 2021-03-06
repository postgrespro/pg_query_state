#!/usr/bin/env sh

if [ -z ${PG_VERSION+x} ]; then
	echo PG_VERSION is not set!
	exit 1
fi

if [ -z ${LEVEL+x} ]; then
	LEVEL=scan-build
fi

if [ -z ${USE_TPCDS+x} ]; then
	USE_TPCDS=0
fi

echo PG_VERSION=${PG_VERSION}
echo LEVEL=${LEVEL}
echo USE_TPCDS=${USE_TPCDS}

sed \
	-e 's/${PG_VERSION}/'${PG_VERSION}/g \
	-e 's/${LEVEL}/'${LEVEL}/g \
	-e 's/${USE_TPCDS}/'${USE_TPCDS}/g \
	Dockerfile.tmpl > Dockerfile
