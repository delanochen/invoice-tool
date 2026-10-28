"""Stream stored ZIP64 members with bounded file-data buffers and backpressure."""

import os
import zipfile


CHUNK_SIZE = 1024 * 1024


class _Output:
    # Deliberately unseekable: zipfile emits data descriptors after each member.
    def __init__(self):
        self.offset = 0
        self.data = bytearray()

    def tell(self):
        return self.offset

    def write(self, data):
        self.data.extend(data)
        self.offset += len(data)
        return len(data)

    def flush(self):
        pass

    def drain(self):
        data = bytes(self.data)
        self.data.clear()
        return data


def stream_attachment_zip(folders, files):
    """Yield ZIP bytes; caller must supply authorized, sanitized file paths.

    Closing the generator closes the current source file. There is no producer
    thread or full-size temporary ZIP; a slow client also slows disk reads.
    ZIP member metadata still grows with the number of files, as required for
    the central directory.
    """
    output = _Output()
    used = set()
    source_paths = set()
    missing = {folder: [] for folder in folders}
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED,
                         allowZip64=True) as archive:
        for folder in folders:
            archive.writestr(folder + '/', b'')
            yield output.drain()
        for folder, path, name in files:
            source_path = path.resolve()
            if source_path in source_paths:
                continue
            source_paths.add(source_path)
            if not path.is_file():
                missing[folder].append(name)
                continue
            stem, suffix = os.path.splitext(name)
            candidate = folder + '/' + name
            index = 2
            while candidate.casefold() in used:
                candidate = f'{folder}/{stem}-{index}{suffix}'
                index += 1
            used.add(candidate.casefold())
            info = zipfile.ZipInfo.from_file(path, arcname=candidate)
            info.compress_type = zipfile.ZIP_STORED
            # force_zip64 permits a member to grow beyond 4 GiB while streaming.
            with path.open('rb') as source, archive.open(info, 'w', force_zip64=True) as member:
                yield output.drain()
                while chunk := source.read(CHUNK_SIZE):
                    member.write(chunk)
                    yield output.drain()
            yield output.drain()
        for folder, names in missing.items():
            if names:
                archive.writestr(folder + '/缺失文件说明.txt',
                                 ('以下附件有记录，但服务器文件不存在：\n' + '\n'.join(names)).encode('utf-8'))
                yield output.drain()
    yield output.drain()
