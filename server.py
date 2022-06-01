import os
import socket
import struct
import sys
import time

FLAGS = _ = None
DEBUG = False


def get_filedict(rootpath):
    files = {}
    with os.scandir(rootpath) as it:
        for entry in it:
            if not entry.name.startswith('.') and entry.is_file():
                info = get_fileinfo(entry.path)
                info['path'] = entry.path
                files[entry.name] = info
    return files


def count_segments(sf, fs):
    count = 0
    counting = True
    while counting:
        fs = (fs + 1) % 2 ** FLAGS.msb
        count = count + 1
        if fs == sf:
            counting = False
    return count


def get_fileinfo(path):
    size = 0
    seq = 0
    segments = []
    with open(path, 'rb') as f:
        while True:
            chunk = f.read(FLAGS.mtu - 1)
            chunk_size = len(chunk)
            if chunk_size == 0:  # if not data:
                break
            segments.append(struct.pack('>B', seq) + chunk)
            seq = (seq + 1) % (2 ** FLAGS.msb)
            size = size + chunk_size
    return {'size': size, 'segments': segments}


def find_cumulative_ack(acks, cur_sn):
    min_cnt = FLAGS.mws + 1
    ret = 0
    for i, ack in enumerate(acks):
        cur_cnt = count_segments(cur_sn, ack) % (2**FLAGS.msb)
        if cur_cnt < min_cnt:
            min_cnt = cur_cnt
            ret = acks[i]
    return ret


def main():
    if DEBUG:
        print(f'Parsed arguments {FLAGS}')
        print(f'Unparsed arguments {_}')

    files = get_filedict(FLAGS.files)
    if DEBUG:
        print(f'Ready to file transfer')
        for key, value in files.items():
            print(f'{key}: {value}')

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((FLAGS.address, FLAGS.port))
    print(f'Listening on {sock}')

    while True:
        try:
            data, client = sock.recvfrom(FLAGS.mtu)
            data = data.decode('utf-8')
            data = data.split(' ')

            if len(data) < 2:
                response = 'Error'
                sock.sendto(response.encode('utf-8'), client)
                continue

            command = data[0].upper()
            target = ' '.join(data[1:])
            print(f'{command} {target} from {client}')

            if target not in files.keys():
                print(f'{target} was not found (requested by {client})')
                response = '404 Not Found'
                sock.sendto(response.encode('utf-8'), client)
                continue

            info = files[target]
            size = info['size']
            segments = info['segments']
            remain = size
            length = len(segments)
            n = 0  # number of segments sent
            SF = 0  # first outstanding segment
            CUR_SN = FLAGS.mws  # first segment of next to send window
            FS = 0  # first segment of a window
            communicating = True
            sending = True
            sliding = False
            goback = False

            if command == 'INFO':
                size_b = str(size).encode('utf-8')
                sock.sendto(size_b, client)
            elif command == 'DOWNLOAD':
                while communicating:
                    i = 0
                    while sending:
                        if n + i < length:
                            segment = segments[n + i]

                            data = segment[1:]
                            seq = segment[:1]
                            chunk = seq + data
                            sock.sendto(chunk, client)

                            data_size = len(data)
                            remain -= data_size
                            print(f'Transferring to {client} with {seq.hex()}: {size - remain}/{size}')
                            i = i + 1
                            if i == FLAGS.mws:  # 윈도우 사이즈만큼 송신
                                i = 0
                                sending = False
                        else:
                            sending = False

                        sock.settimeout(FLAGS.timeout)  # 데이터 송신 후 start timer
                        acks = []
                        while not sending:  # check ack from client
                            try:

                                while True:
                                    sock.settimeout(1)
                                    try:
                                        chunk, client = sock.recvfrom(FLAGS.mtu)
                                        sock.settimeout(None)
                                        ack = struct.unpack('>B', chunk[:1])[0]
                                        print(f'ACK from client: {ack}')
                                        acks.append(ack)
                                        if len(acks) == FLAGS.mws:
                                            break
                                    except socket.timeout:
                                        break

                                SF = find_cumulative_ack(acks, CUR_SN)
                                print(f'cumulative ack : {SF}')
                                sending = True
                                count = count_segments(SF, FS)
                                print(f'prev sn :{FS}')
                                n = n + count
                                FS = SF
                                CUR_SN = (SF + FLAGS.mws) % (2 ** FLAGS.msb)  # SN 초기화
                                print(f'total segments {n}')
                                print(f'Window Sliding SN: {CUR_SN}')

                            except socket.timeout:  # client 가 받지 못한 segment 로 go back
                                SF = find_cumulative_ack(acks, CUR_SN)
                                print(f'cumulative ack : {SF}')
                                sending = True
                                count = count_segments(SF, FS)
                                print(f'prev sn :{FS}')
                                n = n + count
                                print(f'total segments {n}')
                                print(f'Go Back {n}')
                                FS = SF
                                CUR_SN = (SF + FLAGS.mws) % (2 ** FLAGS.msb)  # SN 초기화
                        sock.settimeout(None)

                        if n == length:  # 모든 세그먼트를 보냈다면 통신 종료
                            sending = False
                            communicating = False
                sys.exit()
        except KeyboardInterrupt:
            print(f'Shutting down... {sock}')
            break


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--debug', action='store_true',
                        help='The present debug message')
    parser.add_argument('--address', type=str, default='0.0.0.0',
                        help='The address to serve service')
    parser.add_argument('--port', type=int, default=38442,
                        help='The port to serve service')
    parser.add_argument('--timeout', type=int, default=3,
                        help='The timeout seconds')
    parser.add_argument('--mds', type=int, default=1379,
                        help='The maximum data size')
    parser.add_argument('--mtu', type=int, default=1380,
                        help='The maximum transmission unit')
    parser.add_argument('--files', type=str, default='./files',
                        help='The file directory path')
    parser.add_argument('--msb', type=str, default=4,
                        help='The maximum sequence bit')
    parser.add_argument('--mws', type=str, default=2 ** 4 - 1,
                        help='The maximum sliding window size')

    FLAGS, _ = parser.parse_known_args()
    DEBUG = FLAGS.debug

    FLAGS.files = os.path.abspath(os.path.expanduser(FLAGS.files))

    main()