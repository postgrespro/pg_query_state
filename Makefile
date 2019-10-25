# contrib/pg_query_state/Makefile

MODULE_big = pg_query_state
OBJS = pg_query_state.o signal_handler.o $(WIN32RES)
EXTENSION = pg_query_state
EXTVERSION = 1.1
DATA = pg_query_state--1.0--1.1.sql
DATA_built = $(EXTENSION)--$(EXTVERSION).sql
PGFILEDESC = "pg_query_state - facility to track progress of plan execution"

EXTRA_CLEAN = ./isolation_output $(EXTENSION)--$(EXTVERSION).sql \
	Dockerfile ./tests/*.pyc ./tmp_stress

ifdef USE_PGXS
PG_CONFIG ?= pg_config
PGXS := $(shell $(PG_CONFIG) --pgxs)
include $(PGXS)
else
subdir = contrib/pg_query_state
top_builddir = ../..
include $(top_builddir)/src/Makefile.global
include $(top_srcdir)/contrib/contrib-global.mk
endif

$(EXTENSION)--$(EXTVERSION).sql: init.sql
	cat $^ > $@

check: isolationcheck

ISOLATIONCHECKS=corner_cases

submake-isolation:
	$(MAKE) -C $(top_builddir)/src/test/isolation all

isolationcheck: | submake-isolation temp-install
	$(MKDIR_P) isolation_output
	$(pg_isolation_regress_check) \
	  --temp-config $(top_srcdir)/contrib/pg_query_state/test.conf \
      --outputdir=isolation_output \
	  $(ISOLATIONCHECKS)

isolationcheck-install-force: all | submake-isolation temp-install
	$(MKDIR_P) isolation_output
	$(pg_isolation_regress_installcheck) \
      --outputdir=isolation_output \
	  $(ISOLATIONCHECKS)

.PHONY: isolationcheck isolationcheck-install-force check

temp-install: EXTRA_INSTALL=contrib/pg_query_state
