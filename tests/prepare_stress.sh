#!/bin/sh
mkdir -p tmp_stress
cd tmp_stress
rm -rf ./*
git clone https://github.com/gregrahn/tpcds-kit.git
cd tpcds-kit/tools
make -s

#Generate data
./dsdgen -FORCE -VERBOSE
mkdir tables -p
#Prepare data
for i in `ls *.dat`; do
  echo "Prepare file " $i
  sed 's/|$//' $i > tables/$i
done
#Generate queries
./dsqgen -DIRECTORY ../query_templates -INPUT ../query_templates/templates.lst \
  -VERBOSE Y -QUALIFY Y -DIALECT netezza
