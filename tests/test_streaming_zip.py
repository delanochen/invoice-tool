from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from invoice_tool.attachments.streaming_zip import CHUNK_SIZE, stream_attachment_zip


def test_large_member_is_read_incrementally_and_closed_on_disconnect(tmp_path, monkeypatch):
    path = tmp_path / 'large.jpg'
    with path.open('wb') as output:
        for _ in range(8):
            output.write(b'x' * CHUNK_SIZE)
    original_open = Path.open
    reads, sources = [], []

    class Reader:
        def __enter__(self):
            self.source = original_open(path, 'rb')
            sources.append(self.source)
            return self

        def read(self, size):
            reads.append(size)
            return self.source.read(size)

        def __exit__(self, *args):
            self.source.close()

    monkeypatch.setattr(Path, 'open', lambda *args, **kwargs: Reader())
    stream = stream_attachment_zip(['photos'], [('photos', path, 'large.jpg')])
    assert next(stream).startswith(b'PK')  # directory before any file data is read
    assert reads == []
    next(stream)  # file header
    assert reads == []
    assert len(next(stream)) == CHUNK_SIZE
    assert reads == [CHUNK_SIZE]
    stream.close()  # client disconnects before the remaining 7 MiB are read
    assert sources[0].closed
    assert len(reads) == 1


def test_stored_zip64_archive_crc_duplicates_and_missing_files(tmp_path):
    first = tmp_path / 'first.jpg'
    second = tmp_path / 'second.jpg'
    first.write_bytes(b'a' * (CHUNK_SIZE * 3 + 7))
    second.write_bytes(b'second image')
    chunks = list(stream_attachment_zip(['photos', 'settlement'], [
        ('photos', first, 'image.jpg'),
        ('photos', second, 'IMAGE.jpg'),
        ('settlement', tmp_path / 'missing.pdf', 'receipt.pdf'),
    ]))
    assert max(map(len, chunks)) <= CHUNK_SIZE
    with ZipFile(BytesIO(b''.join(chunks))) as archive:
        assert archive.testzip() is None
        assert archive.read('photos/image.jpg') == first.read_bytes()
        assert archive.read('photos/IMAGE-2.jpg') == second.read_bytes()
        assert b'receipt.pdf' in archive.read('settlement/缺失文件说明.txt')
        assert archive.getinfo('photos/image.jpg').extract_version >= 45
