#!/bin/bash

set -eux

# Update the bash completion
_WORKFLOW_COMPLETE=bash_source workflow > workflow/workflow.bash-completion

# Build the package
flit build
