# Sourced by the external diagnostic recorder; never runs in the Sony UI.
# Four complete snapshots per kind and two bounded index logs, irrespective
# of sample count. OUT is an owned diagnostic directory; BB is BusyBox.
recorder_snapshot() {
    case "$1" in input|journal|trace|ui) ;; *) return 2 ;; esac
    case "$2" in ''|*[!0-9]*) return 2 ;; esac
    _rec_slot=$(($2 % 4))
    _rec_new=$OUT/$1.new
    [ ! -e "$_rec_new" ] || return 3
    if ! "$BB" cp "$3" "$_rec_new"; then
        "$BB" rm -f "$_rec_new"
        return 1
    fi
    if ! "$BB" mv "$_rec_new" "$OUT/$1.$_rec_slot"; then
        "$BB" rm -f "$_rec_new"
        return 1
    fi
}
_rec_file_bytes() {
    _rec_path=$1
    if [ -n "${RECORDER_SIZE_BIN:-}" ] && [ -x "$RECORDER_SIZE_BIN" ]; then
        _rec_out=$("$RECORDER_SIZE_BIN" --size "$_rec_path" 2>/dev/null) || _rec_out=
        case "$_rec_out" in ''|*[!0-9]*) ;; *) _rec_size=$_rec_out; return 0 ;; esac
    fi
    # BusyBox v1.34.1 on ILCE-7RM4A omits wc; ls -ln column 5 is exact bytes.
    _rec_ls=$("$BB" ls -ln "$_rec_path" 2>/dev/null) || return 1
    set -- $_rec_ls
    [ "$#" -ge 5 ] || return 1
    case "$5" in ''|*[!0-9]*) return 1 ;; esac
    _rec_size=$5
}
recorder_log() {
    _rec_message=$*
    _rec_limit=${RECORDER_LOG_LIMIT:-8192}
    case "$_rec_limit" in ''|*[!0-9]*) return 2 ;; esac
    [ "$_rec_limit" -ge 256 ] && [ "$_rec_limit" -le 16384 ] || return 2
    _rec_length=$((${#_rec_message}+1))
    [ "$_rec_length" -le "$_rec_limit" ] || return 2
    _rec_size=0
    if [ -f "$OUT/index.log" ]; then
        _rec_file_bytes "$OUT/index.log" || return 1
    fi
    if [ "$((_rec_size+_rec_length))" -gt "$_rec_limit" ]; then
        "$BB" mv "$OUT/index.log" "$OUT/index.previous.log" || return 1
    fi
    printf '%s\n' "$_rec_message" >> "$OUT/index.log"
}
