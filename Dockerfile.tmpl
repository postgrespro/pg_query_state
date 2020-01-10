FROM postgres:${PG_VERSION}-alpine

# Install dependencies
RUN apk add --no-cache \
	openssl curl git \
	perl perl-ipc-run \
	make musl-dev gcc bison flex coreutils \
	zlib-dev libedit-dev \
	clang clang-analyzer linux-headers \
	python3 python3-dev py3-virtualenv;


# Install fresh valgrind
RUN apk add valgrind \
	--update-cache \
	--repository http://dl-3.alpinelinux.org/alpine/edge/main;

# Environment
ENV LANG=C.UTF-8 PGDATA=/pg/data

# Make directories
RUN	mkdir -p ${PGDATA} && \
	mkdir -p /pg/testdir

# Grant privileges
RUN	chown postgres:postgres ${PGDATA} && \
	chown postgres:postgres /pg/testdir && \
	chmod -R a+rwx /usr/local/lib/postgresql && \
	chmod a+rwx /usr/local/share/postgresql/extension

COPY run_tests.sh /run.sh
RUN chmod 755 /run.sh

ADD . /pg/testdir
WORKDIR /pg/testdir

USER postgres
ENTRYPOINT LEVEL=${LEVEL} USE_TPCDS=${USE_TPCDS} /run.sh
