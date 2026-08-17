#include <arpa/inet.h>
#include <linux/if_ether.h>
#include <linux/if_packet.h>
#include <stdio.h>
#include <sys/socket.h>
#include <unistd.h>

static int exercise_socket(int domain, int type, int protocol,
                           const char *name)
{
    int descriptor = socket(domain, type, protocol);

    if (descriptor < 0) {
        perror(name);
        return 1;
    }
    if (close(descriptor) != 0) {
        perror("close");
        return 1;
    }
    return 0;
}

int main(void)
{
    if (exercise_socket(AF_INET6, SOCK_DGRAM, 0, "socket(AF_INET6)"))
        return 1;
    if (exercise_socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL),
                        "socket(AF_PACKET)"))
        return 1;
    puts("LINUX_MODULARIZER_PROFILE_SOCKET_PATHS_OK");
    return 0;
}
