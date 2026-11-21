"""Private line-oriented prompt adapter, enabled only by the local portal.

The normal terminal workflow never imports this module. Prompt identity travels
on an inherited socket, never inferred from untrusted filenames or output.
"""
import json
import os
import socket
import sys


def ask(prompt):
    print(prompt, end='', flush=True, file=sys.stderr)
    with socket.socket(fileno=os.dup(int(os.environ['MEDIA_SORTER_PROMPT_FD']))) as channel:
        channel.sendall((json.dumps({'prompt': prompt}) + '\n').encode())
        with channel.makefile('rb') as stream:
            line = stream.readline(8193)
        if not line or len(line) > 8192:
            raise EOFError('Prompt connection closed')
        answer = json.loads(line)
        if answer is None:
            raise EOFError('Input ended')
        if not isinstance(answer, str) or len(answer.encode()) > 1024 or any(ord(c) < 32 or ord(c) == 127 for c in answer):
            raise ValueError('Invalid prompt response')
        print(answer, flush=True, file=sys.stderr)
        return answer


if __name__ == '__main__':
    try:
        print(ask(sys.argv[1]))
    except (OSError, ValueError, EOFError):
        sys.exit(1)
