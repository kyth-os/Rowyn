#!/usr/bin/env bash
# rowyn-safe-upgrade — stage a Rowyn system update for the Hub.
#
# The Rowyn-side counterpart of Kyth's kyth-safe-upgrade, reduced to the
# contract the Hub actually exercises (Kyth's rollout-ring, battery, and
# boot-health quarantine policy has no Rowyn equivalent and is not
# ported):
#
#   * invoked with no arguments, as root, via `sudo -A` from the Hub's
#     bootc_upgrade command (any argument is a usage error, exit 64)
#   * progress streams on stdout as marker lines
#         KYTH_STAGE_PROGRESS pct=N phase=P detail=…
#     which the Hub's stage parser consumes verbatim — the KYTH_ prefix
#     is part of that parser contract and is deliberately kept
#   * download occupies pct 1–85, deploy 86–99; 100 is never emitted, so
#     a killed or failed stage can never display as complete
#   * exit 0 on success (update staged, or already up to date) with the
#     result detail as the final stdout text; failures print to stderr
#     and exit non-zero with nothing recorded as staged
#   * the whole stage holds the bootc flock (/run/rowyn-bootc.lock),
#     shared with rowyn-bootc-guard, so two writers can never interleave
#   * SIGTERM (the Hub's cancel) terminates the bootc process group,
#     waits a 10s grace, then kills it — a terminated upgrade never
#     reports staged state
set -uo pipefail

readonly LOCK_FILE=/run/rowyn-bootc.lock
readonly BOOTC_TIMEOUT_SECS=3600
readonly TERM_GRACE_SECS=10
readonly SYSROOT_FREE_MIN_BYTES=$((2 * 1024 * 1024 * 1024))
readonly BOOT_FREE_MIN_BYTES=$((500 * 1024 * 1024))

workdir=
child=
cancelled=0

# Progress state (monotonic: the bar never moves backwards).
last_pct=0
last_phase=
last_detail=
blobs_needed=0
blobs_done=0
deploy_steps=0

cleanup() {
    if [[ -n "$workdir" ]]; then
        rm -rf "$workdir"
    fi
}
trap cleanup EXIT
trap 'cancelled=1' TERM INT HUP

fail() {
    echo "$1" >&2
    exit 1
}

emit_marker() {
    local pct=$1 phase=$2 detail=$3
    if (( pct < last_pct )); then
        return 0
    fi
    if (( pct == last_pct )) && [[ "$phase" == "$last_phase" && "$detail" == "$last_detail" ]]; then
        return 0
    fi
    last_pct=$pct
    last_phase=$phase
    last_detail=$detail
    printf 'KYTH_STAGE_PROGRESS pct=%s phase=%s detail=%s\n' "$pct" "$phase" "$detail"
}

check_free() {
    # Kyth parity: an unmeasurable path is not a failure; a measured
    # shortfall is.
    local path=$1 min_bytes=$2 avail
    avail=$(df --output=avail -B1 "$path" 2>/dev/null | tail -n 1 | tr -d '[:space:]')
    if [[ ! "$avail" =~ ^[0-9]+$ ]]; then
        return 0
    fi
    if (( avail < min_bytes )); then
        echo "Not enough free disk space on $path: $((avail / 1048576)) MiB free, need $((min_bytes / 1048576)) MiB" >&2
        return 1
    fi
    return 0
}

json_number() {
    # First "key":<digits> in a bootc progress JSON line. The closing
    # quote in the pattern keeps "bytes" from matching "bytesCached".
    local line=$1 key=$2
    local re="\"$key\":([0-9]+)"
    if [[ $line =~ $re ]]; then
        printf '%s' "${BASH_REMATCH[1]}"
    fi
}

handle_progress_json() {
    local line=$1
    if [[ "$line" == *'"task":"pulling"'* ]]; then
        if [[ "$line" == *'"type":"ProgressBytes"'* ]]; then
            local bytes total steps steps_total pct layer detail
            bytes=$(json_number "$line" bytes)
            total=$(json_number "$line" bytesTotal)
            steps=$(json_number "$line" steps)
            steps_total=$(json_number "$line" stepsTotal)
            if [[ -n "$total" && "$total" != 0 ]]; then
                pct=$((85 * ${bytes:-0} / total))
            elif [[ -n "$steps_total" && "$steps_total" != 0 ]]; then
                pct=$((85 * ${steps:-0} / steps_total))
            else
                return 0
            fi
            if (( pct < 1 )); then pct=1; fi
            if (( pct > 85 )); then pct=85; fi
            layer=$((${steps:-0} + 1))
            if (( layer > ${steps_total:-1} )); then layer=${steps_total:-1}; fi
            if [[ -n "$total" && "$total" != 0 ]]; then
                detail="Downloading layer $layer of ${steps_total:-1} · $(( ${bytes:-0} / 1048576 )) MiB of $(( total / 1048576 )) MiB"
            elif [[ -n "$steps_total" ]]; then
                detail="Downloading layer $layer of $steps_total"
            else
                detail="Downloading the update"
            fi
            emit_marker "$pct" download "$detail"
        elif [[ "$line" == *'"type":"ProgressSteps"'* ]]; then
            # A retried pull announces itself here; keep the bar where it
            # was and surface bootc's own wording.
            local re='"description":"([^"]*)"'
            if [[ "$line" =~ $re ]]; then
                local shown=$last_pct
                if (( shown < 1 )); then shown=1; fi
                emit_marker "$shown" download "${BASH_REMATCH[1]}"
            fi
        fi
    elif [[ "$line" == *'"task":"importing"'* && "$line" == *'"type":"ProgressSteps"'* ]]; then
        local steps steps_total
        steps=$(json_number "$line" steps)
        steps_total=$(json_number "$line" stepsTotal)
        if [[ -n "$steps" && -n "$steps_total" ]] && (( steps >= steps_total )); then
            emit_marker 88 install "Image downloaded, staging it"
        else
            emit_marker 86 install "Importing the image"
        fi
    fi
}

handle_bootc_text() {
    # Fallback progress from bootc's stderr text (also its only source
    # when an older bootc rejects --progress-fd): layer totals, per-blob
    # completions, and deploy-phase keywords.
    local line=${1,,}
    if [[ "$line" == *"layers needed:"* ]]; then
        local rest=${line#*layers needed:}
        rest=${rest#"${rest%%[![:space:]]*}"}
        local num=${rest%%[!0-9]*}
        if [[ "$num" =~ ^[0-9]+$ ]] && (( num > 0 )); then
            blobs_needed=$num
        fi
        return 0
    fi
    if [[ "$line" == "copying blob "* || "$line" == "copying config "* ]]; then
        if [[ "$line" == *" done"* || "$line" == *"already exists"* || "$line" == *"skipped"* ]]; then
            blobs_done=$((blobs_done + 1))
            if (( blobs_needed > 0 )); then
                local pct=$((85 * blobs_done / blobs_needed))
                if (( pct < 1 )); then pct=1; fi
                if (( pct > 85 )); then pct=85; fi
                emit_marker "$pct" download "Downloading layer $((blobs_done + 1)) of $blobs_needed"
            fi
        fi
        return 0
    fi
    case "$line" in
        *"writing manifest"*|*"storing signatures"*|*"already have manifest"*)
            emit_marker 85 download "Downloading the update"
            ;;
        *"merging layer"*|*"writing commit"*|*deploying*|*dracut*|*bootloader*|*"staged deployment"*|*"staged update"*)
            deploy_steps=$((deploy_steps + 1))
            local steps=$deploy_steps
            if (( steps > 7 )); then steps=7; fi
            emit_marker $((85 + steps * 2)) install "Installing the staged image"
            ;;
    esac
}

# Stream pump: fold newly appended bytes of $1 into the offset/buffer
# variables named by $2/$3 and dispatch each completed line to the
# handler named $4. Command substitution strips trailing newlines, so a
# sentinel keeps line boundaries intact across chunk boundaries; the
# unterminated remainder stays buffered for the next chunk (or the final
# flush in drain_all).
pump() {
    local file=$1
    local -n _offset=$2 _buffer=$3
    local handler=$4
    local size chunk line
    size=$(stat -c %s -- "$file" 2>/dev/null || printf '0')
    if (( size > _offset )); then
        chunk=$(tail -c "+$((_offset + 1))" -- "$file" | tr '\r' '\n'; printf '.')
        chunk=${chunk%.}
        _offset=$size
        _buffer+=$chunk
        while [[ "$_buffer" == *$'\n'* ]]; do
            line=${_buffer%%$'\n'*}
            _buffer=${_buffer#*$'\n'}
            "$handler" "$line"
        done
    fi
}

prog_offset=0
prog_buffer=
err_offset=0
err_buffer=

drain_progress() {
    pump "$workdir/bootc.progress" prog_offset prog_buffer handle_progress_json
    pump "$workdir/bootc.err" err_offset err_buffer handle_bootc_text
}

drain_all() {
    drain_progress
    if [[ -n "$prog_buffer" ]]; then
        handle_progress_json "$prog_buffer"
        prog_buffer=
    fi
    if [[ -n "$err_buffer" ]]; then
        handle_bootc_text "$err_buffer"
        err_buffer=
    fi
}

terminate_child() {
    # The child is a session leader (setsid): signal its whole group so
    # no forked bootc helper outlives the cancel.
    kill -TERM -- "-$child" 2>/dev/null || true
    local waited=0
    while kill -0 "$child" 2>/dev/null && (( waited < TERM_GRACE_SECS * 4 )); do
        sleep 0.25
        waited=$((waited + 1))
    done
    if kill -0 "$child" 2>/dev/null; then
        kill -KILL -- "-$child" 2>/dev/null || true
    fi
    wait "$child" 2>/dev/null || true
}

# Outcome of the last supervised run (globals, so a genuine bootc exit
# code can never collide with the cancellation/timeout sentinels).
child_status=0
child_timed_out=0

# Run `bootc upgrade` supervised: streams progress, enforces the timeout,
# honours cancellation; records the outcome in child_status /
# child_timed_out / cancelled.
run_bootc_upgrade() {
    local with_progress_fd=$1
    : >"$workdir/bootc.out"
    : >"$workdir/bootc.err"
    : >"$workdir/bootc.progress"
    prog_offset=0
    prog_buffer=
    err_offset=0
    err_buffer=
    if (( with_progress_fd )); then
        setsid /usr/bin/bootc upgrade --progress-fd 3 \
            >"$workdir/bootc.out" 2>"$workdir/bootc.err" 3>"$workdir/bootc.progress" &
    else
        setsid /usr/bin/bootc upgrade \
            >"$workdir/bootc.out" 2>"$workdir/bootc.err" &
    fi
    child=$!
    child_status=0
    child_timed_out=0
    local started=$SECONDS
    while kill -0 "$child" 2>/dev/null; do
        drain_progress
        if (( cancelled )); then
            terminate_child
            child_status=1
            return 0
        fi
        if (( SECONDS - started >= BOOTC_TIMEOUT_SECS )); then
            terminate_child
            child_timed_out=1
            child_status=1
            return 0
        fi
        sleep 0.25
    done
    wait "$child"
    child_status=$?
    drain_all
    return 0
}

result_detail() {
    # bootc's own words are the job detail (Kyth relays the same text,
    # redacted and capped; the cap is kept, the redaction patterns are
    # Kyth-service-specific and not ported).
    local detail
    detail=$(cat "$workdir/bootc.out" "$workdir/bootc.err" 2>/dev/null \
        | tr -d '\r' | awk 'NF' | tail -n 5 | head -c 2000)
    printf '%s' "$detail"
}

main() {
    if (( $# > 0 )); then
        echo "rowyn-safe-upgrade accepts no arguments" >&2
        exit 64
    fi
    if (( EUID != 0 )); then
        fail "rowyn-safe-upgrade must run as root"
    fi
    if [[ ! -x /usr/bin/bootc ]]; then
        fail "bootc is not installed on this system"
    fi

    workdir=$(mktemp -d)

    check_free /sysroot "$SYSROOT_FREE_MIN_BYTES" || exit 1
    check_free /boot "$BOOT_FREE_MIN_BYTES" || exit 1

    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        fail "Another bootc upgrade is in progress; retry later"
    fi

    emit_marker 1 download "Starting image download"

    run_bootc_upgrade 1
    if (( ! cancelled && ! child_timed_out && child_status != 0 )); then
        # A bootc that predates --progress-fd rejects it before doing any
        # work. Staging without live byte progress beats not staging.
        if grep -q "unexpected argument '--progress-fd'" "$workdir/bootc.err"; then
            blobs_needed=0
            blobs_done=0
            deploy_steps=0
            run_bootc_upgrade 0
        fi
    fi
    if (( cancelled )); then
        fail "bootc upgrade was terminated and did not complete; nothing was staged"
    fi
    if (( child_timed_out )); then
        fail "bootc upgrade timed out; retry later"
    fi
    if (( child_status != 0 )); then
        local detail
        detail=$(result_detail)
        if [[ -n "$detail" ]]; then
            fail "$detail"
        fi
        fail "bootc upgrade failed (exit code $child_status)"
    fi

    # Did anything actually stage? bootc exits 0 for "no changes" too,
    # and finalizing an empty stage would be an error of its own.
    local status_json compact
    if ! status_json=$(timeout 30 /usr/bin/bootc status --json 2>/dev/null); then
        fail "Could not determine the staged update state after bootc upgrade"
    fi
    compact=${status_json//[[:space:]]/}
    if [[ "$compact" != *'"staged":{'* ]]; then
        local detail
        detail=$(result_detail)
        if [[ -n "$detail" ]]; then
            printf '%s\n' "$detail"
        else
            echo "Rowyn is already running the latest image."
        fi
        exit 0
    fi

    emit_marker 99 verify "Verifying the staged image and checking its safety policy"
    emit_marker 99 finalize "Preparing the staged deployment for the next boot"
    if [[ -x /usr/libexec/rowyn-finalize-staged ]]; then
        if ! /usr/libexec/rowyn-finalize-staged; then
            fail "The staged update could not be finalized for the next boot"
        fi
    elif [[ -x /usr/bin/ostree ]]; then
        if ! /usr/bin/ostree admin finalize-staged; then
            fail "The staged update could not be finalized for the next boot"
        fi
    else
        fail "No staged-update finalizer is installed on this system"
    fi

    local detail
    detail=$(result_detail)
    if [[ -n "$detail" ]]; then
        printf '%s\n' "$detail"
    else
        echo "Update staged and ready to apply on the next boot."
    fi
    exit 0
}

main "$@"
