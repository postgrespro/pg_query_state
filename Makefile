
# contrib/pg_query_state/Makefile

MODULE_big = pg_query_state
OBJS = pg_query_state.o signal_handler.o $(WIN32RES)
EXTENSION = pg_query_state
EXTVERSION = 1.2
DATA = pg_query_state--1.0--1.1.sql \
	   pg_query_state--1.1--1.2.sql
DATA_built = $(EXTENSION)--$(EXTVERSION).sql
PGFILEDESC = "pg_query_state - facility to track progress of plan execution"

EXTRA_CLEAN = ./isolation_output $(EXTENSION)--$(EXTVERSION).sql \
	Dockerfile ./tests/*.pyc ./tmp_stress

ISOLATION_OPTS = --load-extension=pg_query_state

TAP_TESTS = 1

ifdef USE_PGXS
PG_CONFIG ?= pg_config
PGXS := $(shell $(PG_CONFIG) --pgxs)
include $(PGXS)
else
subdir = contrib/pg_query_state
top_builddir = ../..
include $(top_builddir)/src/Makefile.global
include $(top_srcdir)/contrib/contrib-global.mk
# need this to provide make check in case of "in source" build
EXTRA_REGRESS_OPTS=--temp-config=$(top_srcdir)/$(subdir)/test.conf
endif

$(EXTENSION)--$(EXTVERSION).sql: init.sql
	cat $^ > $@

#
# Make conditional targets to save backward compatibility with PG11.
#
ifeq ($(MAJORVERSION),11)
ISOLATIONCHECKS = corner_cases

check: isolationcheck

installcheck: submake-isolation
	$(MKDIR_P) isolation_output
	$(pg_isolation_regress_installcheck) \
	  --outputdir=isolation_output \
	$(ISOLATIONCHECKS)

isolationcheck: | submake-isolation temp-install
	$(MKDIR_P) isolation_output
	$(pg_isolation_regress_check) \
	  --outputdir=isolation_output \
	$(ISOLATIONCHECKS)

submake-isolation:
	$(MAKE) -C $(top_builddir)/src/test/isolation all

temp-install: EXTRA_INSTALL=contrib/pg_query_state
endif
