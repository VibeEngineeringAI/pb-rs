#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <sys/ioctl.h>
#include <unistd.h>
static void winch(int sig) { (void)sig; ssize_t n=write(1,"WINCH\n",6); (void)n; }
int main(void) {
 if(setsid()<0 || ioctl(0,TIOCSCTTY,0)<0 || tcsetpgrp(0,getpgrp())<0) return 2;
 struct sigaction sa={0};sa.sa_handler=winch;sigemptyset(&sa.sa_mask);
 if(sigaction(SIGWINCH,&sa,0)<0) return 3;
 puts("READY");fflush(stdout);
 char c;for(;;){ssize_t n=read(0,&c,1);if(n==1)break;if(n<0&&errno==EINTR)continue;return 4;}return 0;
}
