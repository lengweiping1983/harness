"""在新的解释器中获取控制终端，随后替换进程。

多线程网页服务端不能使用 preexec_fn 或 pty.fork，Python 和 macOS
系统库可能在 fork 时持有锁。此辅助程序改为在 exec 之后
完成少量会话设置。
"""
import fcntl
import os
import sys
import termios


def main() -> None:
    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    os.execvpe(sys.argv[1], sys.argv[1:], os.environ)


if __name__ == '__main__':
    main()
