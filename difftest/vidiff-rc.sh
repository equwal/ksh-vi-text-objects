# ENV for vidiff.py: Ctrl-] (^]) writes the edit line to $VIDIFF_OUT and is dropped
trap '[[ ${.sh.edchar} == *$'"'"'\035'"'"' ]] && { print -r -- "${.sh.edtext}" > "$VIDIFF_OUT"; .sh.edchar=${.sh.edchar%?}; }' KEYBD
