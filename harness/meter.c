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
    while (wait4(pid, &status, 0, &usage) < 0) {
        if (errno != EINTR) { perror("wait4"); return 125; }
    }
    uint64_t ended = ns(CLOCK_MONOTONIC);
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
            "\"voluntary_context_switches\":%ld,\"involuntary_context_switches\":%ld}\n",
            started, ended, started_unix, exit_code,
            WIFSIGNALED(status) ? "signal" : "exit",
            WIFSIGNALED(status) ? WTERMSIG(status) : WEXITSTATUS(status),
            usage.ru_utime.tv_sec * 1000.0 + usage.ru_utime.tv_usec / 1000.0,
            usage.ru_stime.tv_sec * 1000.0 + usage.ru_stime.tv_usec / 1000.0,
            rss, usage.ru_minflt, usage.ru_majflt, usage.ru_nvcsw, usage.ru_nivcsw);
    if (fclose(out) != 0) { perror("metrics close"); return 125; }
    return 0;
}
