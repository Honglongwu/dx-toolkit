# Copyright (C) 2014 DNAnexus, Inc.
#
# This file is part of dx-toolkit (DNAnexus platform client libraries).
#
#   Licensed under the Apache License, Version 2.0 (the "License"); you may not
#   use this file except in compliance with the License. You may obtain a copy
#   of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#   WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#   License for the specific language governing permissions and limitations
#   under the License.

'''
This module provides support for file download and upload. It calculates the
   location of the input and output directories. It also has a utility for parsing
   the job input file ('job_input.json').

We use the following shorthands
   <idir> == input directory     $HOME/in
   <odir> == output directory    $HOME/out

A simple example of the job input, when run locally, is:

{
    "seq2": {
        "$dnanexus_link": {
            "project": "project-1111",
            "id": "file-1111"
        }
    },
    "seq1": {
        "$dnanexus_link": {
            "project": "project-2222",
            "id": "file-2222"
        }
    }
    "blast_args": "",
    "evalue": 0.01
}

The first two elements are files {seq1, seq2}, the other elements are
{blast_args, evalue}. The files for seq1,seq2 should be saved into:
<idir>/seq1/<filename>
<idir>/seq2/<filename>

An example for a shell command that would create these arguments is:
    $ dx run coolapp -iseq1=NC_000868.fasta -iseq2=NC_001422.fasta
It would run an app named "coolapp", with file arguments for seq1 and seq2. Both NC_*
files should be the names of files in a DNAnexus project (and should be resolved to their
file IDs by dx). Subsequently, after dx-download-all-inputs is run,
file seq1 should appear in the execution environment at path:
    <idir>/seq1/NC_000868.fasta

File Arrays

{
    "reads": [{
        "$dnanexus_link": {
            "project": "project-3333",
            "id": "file-3333"
        }
    },
    {
        "$dnanexus_link": {
            "project": "project-4444",
            "id": "file-4444"
        }
    }]
}

This is a file array with two files. Running a command like this:
    $ dx run coolapp -ireads=A.fastq -ireads=B.fasta
will download into the execution environment:
<idir>/reads/A.fastq
             B.fastq

'''

import json
import pipes
import os
import math
import sys
import collections
import dxpy
from ..exceptions import DXError

def get_input_dir():
    '''
    :rtype : string
    :returns : path to input directory

    Returns the input directory, where all inputs are downloaded
    '''
    home_dir = os.environ.get('HOME')
    idir = os.path.join(home_dir, 'in')
    return idir

def get_relative_input_dir():
    '''
    :rtype : string
    :returns : relative path to input directory

    Returns the input directory, where all inputs are downloaded, relative to HOME
    '''
    return "$HOME/in"

def get_output_dir():
    '''
    :rtype : string
    :returns : path to output directory

    Returns the output directory, where all outputs are created, and
    uploaded from
    '''
    home_dir = os.environ.get('HOME')
    odir = os.path.join(home_dir, 'out')
    return odir

def get_input_json_file():
    """
    :rtype : string
    :returns: path to input JSON file
    """
    home_dir = os.environ.get('HOME')
    return os.path.join(home_dir, "job_input.json")

def get_output_json_file():
    """
    :rtype : string
    :returns : Path to output JSON file
    """
    home_dir = os.environ.get('HOME')
    return os.path.join(home_dir, "job_output.json")

def rm_output_json_file():
    """ Warning: this is not for casual use.
    It erases the output json file, and should be used for testing purposes only.
    """
    path = get_output_json_file()
    try:
        os.remove(path)
    except OSError as e:
        if e.errno == errno.ENOENT:
            pass
        else:
            raise

def ensure_dir(path):
    """
    :param path: path to directory to be created

    Create a directory if it does not already exist.
    """
    if not os.path.exists(path):
        # path does not exist, create the directory
        os.mkdir(path)
    else:
        # The path exists, check that it is not a file
        if os.path.isfile(path):
            raise Exception("Path %s already exists, and it is a file, not a directory" % path)

def make_unix_filename(fname):
    """
    :param fname: the basename of a file (e.g., xxx in /zzz/yyy/xxx).
    :return: a valid unix filename
    :rtype: string
    :raises DXError: if the filename is invalid on a Unix system

    The problem being solved here is that *fname* is a python string, it
    may contain characters that are invalid for a file name. We replace all the slashes with %2F.
    Another issue, is that the user may choose an invalid name. Since we focus
    on Unix systems, the only possibilies are "." and "..".
    """
    # sanity check for filenames
    bad_filenames = [".", ".."]
    if fname in bad_filenames:
        raise DXError("Invalid filename {}".format(fname))
    return fname.replace('/', '%2F')

## filter from a dictionary a list of matching keys
def filter_dict(dict_, excl_keys):
    return {k: v for k, v in dict_.iteritems() if k not in excl_keys}

def get_job_input_filenames(job_input_file):
    """Extract list of files, returns a set of directories to create, and
    a set of files, with sources and destinations. The paths created are
    relative to the input directory.

    Note: we go through file names inside arrays, and create a
    separate subdirectory for each. This avoids clobbering files when
    duplicate filenames appear in an array.
    """
    def get_input_hash():
        with open(job_input_file) as fh:
            job_input = json.load(fh)
            return job_input
    job_input = get_input_hash()

    files = collections.defaultdict(list)  # dictionary, with empty lists as default elements
    dirs = []  # directories to create under <idir>

    # Local function for adding a file to the list of files to be created
    # for example:
    #    iname == "seq1"
    #    subdir == "015"
    #    value == { "$dnanexus_link": {
    #       "project": "project-BKJfY1j0b06Z4y8PX8bQ094f",
    #       "id": "file-BKQGkgQ0b06xG5560GGQ001B"
    #    }
    # will create a record describing that the file should
    # be downloaded into seq1/015/<filename>
    def add_file(iname, subdir, value):
        if not dxpy.is_dxlink(value):
            return
        handler = dxpy.get_handler(value)
        if not isinstance(handler, dxpy.DXFile):
            return
        filename = make_unix_filename(handler.name)
        trg_dir = iname
        if subdir is not None:
            trg_dir = os.path.join(trg_dir, subdir)
        files[iname].append({'trg_fname': os.path.join(trg_dir, filename),
                             'handler': handler,
                             'src_file_id': handler.id})
        dirs.append(trg_dir)

    # An array of inputs, for a single key. A directory
    # will be created per array entry. For example, if the input key is
    # FOO, and the inputs are {A, B, C}.vcf then, the directory structure
    # will be:
    #   <idir>/FOO/00/A.vcf
    #   <idir>/FOO/01/B.vcf
    #   <idir>/FOO/02/C.vcf
    def add_file_array(input_name, links):
        num_files = len(links)
        if num_files == 0:
            return
        num_digits = len(str(num_files - 1))
        dirs.append(input_name)
        for i, link in enumerate(links):
            subdir = str(i).zfill(num_digits)
            add_file(input_name, subdir, link)

    for input_name, value in job_input.iteritems():
        if isinstance(value, list):
            # This is a file array
            add_file_array(input_name, value)
        else:
            add_file(input_name, None, value)

    ## create a dictionary of the all non-file elements
    rest_hash = {}
    for input_name, value in job_input.iteritems():
        if input_name not in files:
            rest_hash[input_name] = value
    return dirs, files, rest_hash

def analyze_bash_vars(job_input_file):
    '''
    This function examines the input file, and calculates variables to
    instantiate in the shell environment. It is called right before starting the
    exeuction of an app in a worker.

    For each input key, we want to have
    $var
    $var_filename
    $var_prefix
       remove last dot (+gz), and/or remove patterns
    $var_path
       $HOME/in/var/$var_filename

    For example,
    $HOME/in/genes/A.txt
                   B.txt

    export genes=("$dnanexus_link {id: file-xxxx}" "$dnanexus_link {id: file-yyyy}")
    export genes_filename=(A.txt B.txt)
    export genes_prefix=(A B)
    export genes_path=("$home/in/genes/A.txt" "$home/in/genes/B.txt")
'''
    dirs,file_entries,rest_hash = get_job_input_filenames(job_input_file)
    def factory():
        return {'handler': [], 'filename': [],  'prefix': [], 'path': []}
    file_key_descs = collections.defaultdict(factory)
    rel_home_dir = get_relative_input_dir()
    for key, entries in file_entries.iteritems():
        for entry in entries:
            filename = entry['trg_fname']
            basename = os.path.basename(filename)
            prefix = os.path.splitext(basename)[0]
            k_desc = file_key_descs[key]
            k_desc['handler'].append(entry['handler'])
            k_desc['filename'].append(basename)
            k_desc['prefix'].append(prefix)
            k_desc['path'].append(os.path.join(rel_home_dir, filename))
    return file_key_descs, rest_hash

#
# Note: pipes.quote() to be replaced with shlex.quote() in Python 3 (see http://docs.python.org/2/library/pipes.html#pipes.quote)
# TODO: Detect and warn about collisions with essential environment variables
def gen_lines_for_bash_vars(job_input_file):
    file_key_descs,rest_hash = analyze_bash_vars(job_input_file)

    def string_of_elem(elem):
        if isinstance(elem, basestring):
            return '"{}"'.format(elem )
        elif isinstance(elem, dxpy.DXFile):
            ln = dxpy.dxlink(elem, project_id=elem.get_proj_id())
            return pipes.quote(json.dumps(ln))
        else:
            return pipes.quote(json.dumps(elem))

    def string_of_list(val_list):
        if len(val_list) > 1:
            str = " ".join([string_of_elem(vitem) for vitem in val_list])
            return "( {} )".format(str)
        else:
            return string_of_elem(val_list[0])

    lines = []
    for file_key,desc in file_key_descs.iteritems():
        lines.append("export {}={}".format(file_key, string_of_list(desc['handler'])))
        lines.append("export {}_filename={}".format(file_key, string_of_list(desc['filename'])))
        lines.append("export {}_prefix={}".format(file_key, string_of_list(desc['prefix'])))
        lines.append("export {}_path={}".format(file_key, string_of_list(desc['path'])))
    for key,desc in rest_hash.iteritems():
        lines.append("export {}={}".format(key, string_of_elem(desc)))
    return lines

def original_hash_for_bash_vars(input_hash):
    return "\n".join(
        ["export {k}=( {vlist} )".format(k=k, vlist=" ".join([pipes.quote(vitem if isinstance(vitem, basestring) else json.dumps(vitem)) for vitem in v])) if isinstance(v, list) else "export {k}={v}".format(k=k, v=pipes.quote(v if isinstance(v, basestring) else json.dumps(v))) for k, v in input_hash.items()])

