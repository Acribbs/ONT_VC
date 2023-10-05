"""
=================
Pipeline variantcalling
=================


Overview
==================

This workflow processes nanopore fastq files. The aim of this pipeline
is to generate a lits of variants associated with each sample

Usage
=====

To generate the config file to change the running of the pipeline you need to
run:

ontvc variantcalling config

This will generate a pipeline.yml file that the user can modify to change the
output of the pipeline. Once the user has modified the pipeline.yml file the
pipeline can then be ran using the following commandline command:

ontvc variantcalling make full -v5

You can run the pipeline locally (without a cluster) using --local

ontvc variantcalling make full -v5 --local

Configuration
-------------

The pipeline uses CGAT-core as the pipeline language. Please see the
docuemntation for how to install ontvc.


Input files
-----------

The workflow requires the following inputs:
* a single fastq file generated by guppy basecalling

Pipeline output
==================

Code
==================
"""
import sys
import os
import pysam
import glob
import pandas as pd
from ruffus import *
import cgatcore.iotools as iotools
import cgatcore.pipeline as P
import cgatcore.experiment as E
from cgatcore.pipeline import cluster_runnable

# load options from the config file
PARAMS = P.get_parameters(
    ["%s/pipeline.yml" % os.path.splitext(__file__)[0],
     "../pipeline.yml",
     "pipeline.yml"])

# Determine the location of the input fastq files

try:
    PARAMS['data']
except NameError:
    DATADIR = "."
else:
    if PARAMS['data'] == 0:
        DATADIR = "."
    elif PARAMS['data'] == 1:
        DATADIR = "data.dir"
    else:
        DATADIR = PARAMS['data']


SEQUENCESUFFIXES = ("*.fastq.gz",
		    "*.fastq.1.gz")
SEQUENCEFILES = tuple([os.path.join(DATADIR, suffix_name)
                       for suffix_name in SEQUENCESUFFIXES])

@follows(mkdir("mapped.dir"))
@transform(SEQUENCEFILES,
           regex("{}/(\S+).fastq.gz".format(DATADIR)),
         r"mapped.dir/\1_sorted.bam")
def run_mapping(infile, outfile):
    '''Run minimap2 to map the data to genome'''

    tmp = outfile.replace("_sorted.bam", ".sam")
    bamfile = tmp.replace(".sam", ".bam")

    statement = '''minimap2 -t 4 %(minimap2_options)s %(reference_fasta)s %(infile)s > %(tmp)s &&
                   samtools view -S -b %(tmp)s > %(bamfile)s &&
                   samtools sort %(bamfile)s -o %(outfile)s &&
                   samtools index %(outfile)s &&
                '''

    P.run(statement, job_threads=4, job_options='-t 24:00:00')


@follows(mkdir("Clair.dir"))
@transform(run_mapping,
           regex("mapped.dir/(\S+)_sorted.bam"),
           r"Clair.dir/\1/full_alignment.vcf.gz")
def run_clair3(infile, outfile):
    '''Run the clair3 model for variant calling '''

    outfile_path = outfile.replace('/tmp.txt','')

    statement = '''run_clair3.sh --bam_fn=%(infile)s --ref_fn=%(reference_fasta)s --threads=5 --platform="ont" --model_path=%(clair_model)s --output=%(outfile_path)s && touch %(outfile)s'''

    P.run(statement, job_queue='gpu', job_options='-t 24:00:00')


@follows(mkdir("filtered_vcf.dir"))
@transform(run_clair3,
           regex("Clair.dir/(\S+)/full_alignment.vcf.gz/pileup.vcf.gz"),
           r"filtered_vcf.dir/\1_Qual30_full_alignment.vcf.gz")
def filter_variants(infile, outfile):
    '''use bcftools to filter variants'''

    statement = '''bcftools filter -O z -o %(outfile)s -i "QUAL>20 & DP>20" %(infile)s'''

    P.run(statement, job_options='-t 24:00:00')


@follows(mkdir("Sniffles.dir"))
@transform(run_mapping,
           regex("mapped.dir/(\S+)_sorted.bam"),
           r"Sniffles.dir/\1/output.snf")
def run_sniffles(infile, outfile):
    '''Run the sniffles for structural variants'''

    outfile_path = outfile.replace('/tmp.txt','')
    vcf_file = outfile.replace('.snf','.vcf')
    log = outfile + ".log"

    statement = '''sniffles -i %(infile)s --vcf %(vcf_file)s --snf %(outfile)s --reference %(reference_fasta)s 2> %(log)s'''

    P.run(statement, job_options='-t 48:00:00')


@merge(run_sniffles,
       "Sniffles.dir/merged.vcf.gz")
def merge_sniffles(infiles, outfile):
    '''Merge snf files into a single merged vcf '''

    infiles = ' '.join(infiles)

    statement = '''sniffles --input %(infiles)s --vcf %(outfile)s'''

    P.run(statement)


@transform(run_sniffles,
           regex("Sniffles.dir/(\S+)/output.snf"),
           r"filtered_vcf.dir/\1_sniffles_Qual30_output.vcf.gz")
def merge_sniffles_variants(infile, outfile):
    '''use bcftools to filter variants'''

    infile = infile.replace('.snf','.vcf')

    statement = '''bcftools filter -O z -o %(outfile)s -i "QUAL>30" %(infile)s'''

    P.run(statement)


@follows(mkdir("coverage.dir"))
@transform(run_mapping,
           regex("mapped.dir/(\S+)_sorted.bam"),
           r"coverage.dir/\1.mosdepth.summary.txt")
def mosdepth(infile, outfile):
    '''Determine the coverage of the bam file'''

    name = outfile.replace(".mosdepth.summary.txt","")

    statement = '''mosdepth %(name)s %(infile)s '''

    P.run(statement, job_options='-t 24:00:00')


@transform(run_mapping,
           regex("mapped.dir/(\S+)_sorted.bam"),
           r"mapped.dir/\1.bw")
def run_bamcoverage(infile, outfile):
    ''' '''

    statement = '''bamCoverage -b %(infile)s -o %(outfile)s '''

    P.run(statement, job_options='-t 24:00:00')


@follows(mosdepth, merge_sniffles_variants, filter_variants, run_bamcoverage)
def full():
    pass


def main(argv=None):
    if argv is None:
        argv = sys.argv
    P.main(argv)


if __name__ == "__main__":
    sys.exit(P.main(sys.argv))    
