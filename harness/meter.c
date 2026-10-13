/* Minimal POSIX supervisor: measure the actual CLI, not Python's inherited RSS.
 * Compile with: cc -O2 -Wall -Wextra -o bin/pb-meter harness/meter.c
 * No output is written to the CLI's stdout/stderr. Resource collection and
 * timing start after this supervisor has exec'd, immediately before fork.
 */
#define _DEFAULT_SOURCE
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <inttypes.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static uint64_t ns(clockid_t clock) {
    struct timespec value;
    if (clock_gettime(clock, &value) != 0) { perror("clock_gettime"); exit(125); }
    return (uint64_t)value.tv_sec * 1000000000ULL + (uint64_t)value.tv_nsec;
}


/* Read counters while the exited Linux child is still waitable. WNOWAIT keeps
 * /proc/PID/io available until the final wait4 collects the original rusage.
 * syscr/syscw and rchar/wchar cover read/write-family accounting, not all IO
 * syscalls: recv/send paths are excluded. Different runtimes use different socket
 * calls, so these cannot compare network IO. Storage bytes exclude network traffic.
 * Availability is explicit: missing or denied counters never become zero.
 */
static const char *io_json_keys[] = {"proc_io_syscr", "proc_io_syscw", "proc_io_rchar", "proc_io_wchar", "proc_io_read_bytes", "proc_io_write_bytes", "proc_io_cancelled_write_bytes"};
struct child_io {
    uint64_t values[7];
    int available;
    const char *reason;
};

#ifdef __linux__
static const char *io_keys[] = {"syscr", "syscw", "rchar", "wchar", "read_bytes", "write_bytes", "cancelled_write_bytes"};
static void collect_child_io(pid_t pid, struct child_io *metrics) {
    char path[64];
    snprintf(path, sizeof(path), "/proc/%ld/io", (long)pid);
    FILE *source = fopen(path, "r");
    if (!source) {
        metrics->reason = errno == EACCES || errno == EPERM ? "permission_denied" : "process_io_unavailable";
        return;
    }
    unsigned found = 0;
    char line[256];
    while (fgets(line, sizeof(line), source)) {
        char key[64];
        uint64_t value;
        if (sscanf(line, "%63[^:]: %" SCNu64, key, &value) != 2) continue;
        for (unsigned i = 0; i < 7; i++) {
            if (strcmp(key, io_keys[i]) == 0) {
                metrics->values[i] = value;
                found |= 1U << i;
            }
        }
    }
    int failed = ferror(source);
    if (fclose(source) != 0) failed = 1;
    if (failed) { metrics->reason = "process_io_read_error"; return; }
    if (found != 127U) { metrics->reason = "process_io_fields_missing"; return; }
    metrics->available = 1;
    metrics->reason = NULL;
}
#endif

int main(int argc, char **argv) {
    if (argc < 4 || argv[2][0] != '-' || argv[2][1] != '-' || argv[2][2] != '\0') {
        fputs("usage: pb-meter METRICS.json -- EXECUTABLE [ARG ...]\n", stderr);
        return 125;
    }
    /* A group SIGINT goes to both processes; retain the supervisor to reap the
     * CLI's real wait status. The exec'd CLI gets ordinary default dispositions.
     */
    signal(SIGINT, SIG_IGN);
    signal(SIGTERM, SIG_IGN);
    signal(SIGPIPE, SIG_IGN);
    uint64_t started_unix = ns(CLOCK_REALTIME);
    uint64_t started = ns(CLOCK_MONOTONIC);
    pid_t pid = fork();
    if (pid < 0) { perror("fork"); return 125; }
    if (pid == 0) {
        signal(SIGINT, SIG_DFL);
        signal(SIGTERM, SIG_DFL);
        signal(SIGPIPE, SIG_DFL);
        execv(argv[3], &argv[3]);
        perror("execv");
        _exit(127);
    }
    int status;
    struct rusage usage;
    struct child_io io = {{0}, 0, "unsupported_platform"};
    uint64_t ended;
#ifdef __linux__
    siginfo_t child_exit;
    memset(&child_exit, 0, sizeof(child_exit));
    while (waitid(P_PID, (id_t)pid, &child_exit, WEXITED | WNOWAIT) < 0) {
        if (errno != EINTR) { perror("waitid"); return 125; }
    }
    /* End latency before reading /proc or writing measurement artifacts. */
    ended = ns(CLOCK_MONOTONIC);
    collect_child_io(pid, &io);
#endif
    while (wait4(pid, &status, 0, &usage) < 0) {
        if (errno != EINTR) { perror("wait4"); return 125; }
    }
#ifndef __linux__
    ended = ns(CLOCK_MONOTONIC);
#endif
    uint64_t rss = (uint64_t)usage.ru_maxrss;
#ifndef __APPLE__
    rss *= 1024;
#endif
    FILE *out = fopen(argv[1], "w");
    if (!out) { perror("metrics output"); return 125; }
    int exit_code = WIFSIGNALED(status) ? -WTERMSIG(status) : WEXITSTATUS(status);
    fprintf(out, "{\"started_monotonic_ns\":%" PRIu64 ",\"ended_monotonic_ns\":%" PRIu64
            ",\"started_unix_ns\":%" PRIu64 ",\"exit_code\":%d,\"termination\":{\"%s\":%d},"
            "\"cpu_user_ms\":%.6f,\"cpu_system_ms\":%.6f,\"peak_rss_bytes\":%" PRIu64
            ",\"minor_faults\":%ld,\"major_faults\":%ld,"
            "\"voluntary_context_switches\":%ld,\"involuntary_context_switches\":%ld,"
            "\"block_input_operations\":%ld,\"block_output_operations\":%ld,"
            "\"resource_source\":\"native_child_wait4\"",
            started, ended, started_unix, exit_code,
            WIFSIGNALED(status) ? "signal" : "exit",
            WIFSIGNALED(status) ? WTERMSIG(status) : WEXITSTATUS(status),
            usage.ru_utime.tv_sec * 1000.0 + usage.ru_utime.tv_usec / 1000.0,
            usage.ru_stime.tv_sec * 1000.0 + usage.ru_stime.tv_usec / 1000.0,
            rss, usage.ru_minflt, usage.ru_majflt, usage.ru_nvcsw, usage.ru_nivcsw,
            usage.ru_inblock, usage.ru_oublock);
    fprintf(out, ",\"proc_io_available\":%s,\"proc_io_unavailable_reason\":", io.available ? "true" : "false");
    if (io.available) fputs("null", out);
    else fprintf(out, "\"%s\"", io.reason);
    fputs(",\"proc_io_source\":", out);
    if (io.available) fputs("\"linux_proc_pid_io_before_wait4\"", out);
    else fputs("null", out);
    for (unsigned i = 0; i < 7; i++) {
        fprintf(out, ",\"%s\":", io_json_keys[i]);
        if (io.available) fprintf(out, "%" PRIu64, io.values[i]);
        else fputs("null", out);
    }
    fputs("}\n", out);
    if (fclose(out) != 0) { perror("metrics close"); return 125; }
    return 0;
}
