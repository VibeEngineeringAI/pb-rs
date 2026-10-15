/* Linux phase7 supervisor: fd3 is a duplex private protocol/release socket.
 * A fresh native fork isolates CLI wait4 from the Python observer image.
 * WNOWAIT reserves the exited CLI PID/group until the observer releases it.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static volatile sig_atomic_t cli_pid;
static volatile sig_atomic_t stopped;
static void stop(int number) {
    (void)number;
    stopped = 1;
    if (cli_pid > 0) {
        kill(-(pid_t)cli_pid, SIGKILL);
        kill((pid_t)cli_pid, SIGKILL);
    }
}
static uint64_t now_ns(void) {
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC, &value)) _exit(125);
    return (uint64_t)value.tv_sec * 1000000000ULL + (uint64_t)value.tv_nsec;
}
static int report(const char *buffer, size_t length) {
    while (length) {
        ssize_t count = write(3, buffer, length);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return -1;
        buffer += count;
        length -= (size_t)count;
    }
    return 0;
}
enum stage { SIGNALS = 1, PARENT, SESSION, CTTY, DIRECTORY, EXEC };
static const char *stage_name(int stage) {
    switch (stage) {
        case SIGNALS: return "signals";
        case PARENT: return "parent";
        case SESSION: return "setsid";
        case CTTY: return "ctty";
        case DIRECTORY: return "chdir";
        case EXEC: return "exec";
        default: return "unknown";
    }
}
struct exec_failure { int stage; int error; };
static void child_fail(int fd, int stage, int error) {
    struct exec_failure failure = {stage, error};
    const char *data = (const char *)&failure;
    size_t left = sizeof(failure);
    while (left) {
        ssize_t count = write(fd, data, left);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) break;
        data += count;
        left -= (size_t)count;
    }
    dprintf(2, "phase7 exec failure: %s: %s\n", stage_name(stage), strerror(error));
    _exit(127);
}
static void child_signals(int error_fd) {
    /* Explicit fixture policy, not a blanket reset of inherited ignores. */
    const int defaults[] = {SIGINT, SIGTERM, SIGHUP, SIGQUIT, SIGPIPE,
#ifdef SIGXFZ
        SIGXFZ,
#endif
#ifdef SIGXFSZ
        SIGXFSZ,
#endif
    };
    struct sigaction action = {0};
    action.sa_handler = SIG_DFL;
    sigemptyset(&action.sa_mask);
    for (size_t i = 0; i < sizeof(defaults) / sizeof(defaults[0]); i++)
        if (sigaction(defaults[i], &action, NULL)) child_fail(error_fd, SIGNALS, errno);
    sigset_t empty;
    sigemptyset(&empty);
    if (sigprocmask(SIG_SETMASK, &empty, NULL)) child_fail(error_fd, SIGNALS, errno);
}
/* Functional capability probe: both members must receive the pidfd group signal.
 * This is an internal harness executable mode, never a production CLI flag. */
static volatile sig_atomic_t probe_hit, probe_member;
static void probe_signal(int number) {
    (void)number;
    const char *text = probe_member ? "MEMBER\n" : "LEADER\n";
    write(1, text, 7);
    probe_hit = 1;
}
static int group_probe(void) {
    struct sigaction action = {0};
    action.sa_handler = probe_signal;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGUSR1, &action, NULL)) return 125;
    sigset_t blocked, prior;
    sigemptyset(&blocked);
    sigaddset(&blocked, SIGUSR1);
    if (sigprocmask(SIG_BLOCK, &blocked, &prior)) return 125;
    int ready[2];
    if (pipe(ready)) return 125;
    pid_t leader = getpid();
    pid_t member = fork();
    if (member < 0) return 125;
    if (!member) {
        close(ready[0]);
        probe_member = 1;
        if (prctl(PR_SET_PDEATHSIG, SIGKILL) || getppid() != leader) _exit(125);
        if (write(ready[1], "R", 1) != 1) _exit(125);
        close(ready[1]);
        while (!probe_hit) sigsuspend(&prior);
        _exit(0);
    }
    close(ready[1]);
    char byte;
    ssize_t count;
    do { count = read(ready[0], &byte, 1); } while (count < 0 && errno == EINTR);
    close(ready[0]);
    if (count != 1 || write(1, "READY\n", 6) != 6) return 125;
    while (!probe_hit) sigsuspend(&prior);
    int status;
    while (waitpid(member, &status, 0) < 0) { if (errno != EINTR) return 125; }
    return WIFEXITED(status) ? WEXITSTATUS(status) : 125;
}
int main(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1], "signal-probe")) return group_probe();
    if (argc == 2 && !strcmp(argv[1], "signal-state")) {
        FILE *file = fopen("/proc/self/status", "r");
        if (!file) return 125;
        char line[256];
        while (fgets(line, sizeof(line), file))
            if (!strncmp(line, "SigIgn:", 7) || !strncmp(line, "SigBlk:", 7)) fputs(line, stdout);
        return fclose(file) ? 125 : 0;
    }
    if (argc < 5 || (strcmp(argv[1], "pty") && strcmp(argv[1], "pipe"))) return 125;
    char *end;
    errno = 0;
    long expected_parent = strtol(argv[2], &end, 10);
    if (errno || *end || expected_parent <= 0 || getppid() != expected_parent) return 125;
    struct sigaction action = {0};
    action.sa_handler = stop;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGTERM, &action, NULL) || sigaction(SIGINT, &action, NULL) ||
        sigaction(SIGHUP, &action, NULL)) return 125;
    signal(SIGPIPE, SIG_IGN);
    if (prctl(PR_SET_PDEATHSIG, SIGTERM) || getppid() != expected_parent || stopped) return 125;
    int ready[2];
    if (pipe(ready) || fcntl(ready[1], F_SETFD, FD_CLOEXEC) < 0) return 125;
    uint64_t started = now_ns();
    pid_t supervisor = getpid();
    pid_t pid = fork();
    if (pid < 0) return 125;
    if (pid == 0) {
        close(ready[0]);
        close(3);
        child_signals(ready[1]);
        if (prctl(PR_SET_PDEATHSIG, SIGKILL)) child_fail(ready[1], PARENT, errno);
        if (getppid() != supervisor) child_fail(ready[1], PARENT, ECHILD);
        if (setsid() < 0) child_fail(ready[1], SESSION, errno);
        if (!strcmp(argv[1], "pty") && ioctl(0, TIOCSCTTY, 0) < 0)
            child_fail(ready[1], CTTY, errno);
        if (chdir(argv[3])) child_fail(ready[1], DIRECTORY, errno);
        execv(argv[4], &argv[4]);
        child_fail(ready[1], EXEC, errno);
    }
    cli_pid = pid;
    if (stopped) stop(SIGTERM);
    close(ready[1]);
    close(0); close(1); close(2);
    struct exec_failure failure = {0};
    size_t received = 0;
    while (received < sizeof(failure)) {
        ssize_t count = read(ready[0], (char *)&failure + received, sizeof(failure) - received);
        if (count < 0 && errno == EINTR) continue;
        if (count < 0) { stop(SIGTERM); failure.stage = EXEC; failure.error = EIO; break; }
        if (count == 0) break;
        received += (size_t)count;
    }
    close(ready[0]);
    if (received && received != sizeof(failure)) { failure.stage = EXEC; failure.error = EIO; }
    char buffer[1536];
    int count;
    if (failure.stage)
        count = snprintf(buffer, sizeof(buffer), "{\"event\":\"exec_error\",\"protocol_version\":2,"
            "\"pid\":%ld,\"started_monotonic_ns\":%" PRIu64 ",\"stage\":\"%s\",\"errno\":%d}\n",
            (long)pid, started, stage_name(failure.stage), failure.error);
    else
        count = snprintf(buffer, sizeof(buffer), "{\"event\":\"spawn\",\"protocol_version\":2,"
            "\"pid\":%ld,\"started_monotonic_ns\":%" PRIu64 "}\n", (long)pid, started);
    if (report(buffer, (size_t)count)) stop(SIGTERM);
    siginfo_t info = {0};
    while (waitid(P_PID, (id_t)pid, &info, WEXITED | WNOWAIT)) {
        if (errno != EINTR) return 125;
    }
    uint64_t exited = now_ns();
    count = snprintf(buffer, sizeof(buffer), "{\"event\":\"exited\",\"pid\":%ld,"
        "\"exited_monotonic_ns\":%" PRIu64 "}\n", (long)pid, exited);
    if (report(buffer, (size_t)count)) stop(SIGTERM);
    /* EOF also releases: observer death must not strand an unreaped CLI. */
    char release;
    while (read(3, &release, 1) < 0) {
        if (errno == EINTR) continue;
        stop(SIGTERM);
        break;
    }
    /* No numeric-PID signal handler may run between reap and cli_pid=0. */
    sigset_t blocked;
    sigemptyset(&blocked);
    sigaddset(&blocked, SIGTERM); sigaddset(&blocked, SIGINT); sigaddset(&blocked, SIGHUP);
    if (sigprocmask(SIG_BLOCK, &blocked, NULL)) return 125;
    int status;
    struct rusage usage;
    while (wait4(pid, &status, 0, &usage) < 0) {
        if (errno != EINTR) return 125;
    }
    cli_pid = 0;
    uint64_t reaped = now_ns();
    count = snprintf(buffer, sizeof(buffer), "{\"event\":\"exit\",\"pid\":%ld,\"status\":%d,"
        "\"exited_monotonic_ns\":%" PRIu64 ",\"reaped_monotonic_ns\":%" PRIu64 ","
        "\"ru_utime\":%.6f,\"ru_stime\":%.6f,\"ru_maxrss\":%ld,"
        "\"ru_minflt\":%ld,\"ru_majflt\":%ld,\"ru_nvcsw\":%ld,\"ru_nivcsw\":%ld,"
        "\"ru_inblock\":%ld,\"ru_oublock\":%ld}\n", (long)pid, status, exited, reaped,
        usage.ru_utime.tv_sec + usage.ru_utime.tv_usec / 1000000.0,
        usage.ru_stime.tv_sec + usage.ru_stime.tv_usec / 1000000.0,
        usage.ru_maxrss, usage.ru_minflt, usage.ru_majflt, usage.ru_nvcsw, usage.ru_nivcsw,
        usage.ru_inblock, usage.ru_oublock);
    return report(buffer, (size_t)count) ? 125 : 0;
}
