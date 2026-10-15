from pathlib import Path
from zipfile import ZipFile

from django.core.exceptions import ValidationError


def validate_document(upload):
    """Check size and document signatures; never trust the supplied MIME type."""
    if not upload.size or upload.size > 5 * 1024 * 1024:
        raise ValidationError('Choose a nonempty document no larger than 5 MB.')
    extension = Path(upload.name).suffix.lower()
    try:
        header = upload.read(1024)
        upload.seek(0)
        if extension == '.pdf':
            valid = header.startswith(b'%PDF-') and b'%%EOF' in upload.read()[-1024:]
        elif extension == '.doc':
            # Word binary files use the OLE container and a WordDocument stream.
            data = upload.read()
            valid = (
                header.startswith(bytes.fromhex('D0CF11E0A1B11AE1'))
                and 'WordDocument'.encode('utf-16le') in data
            )
        elif extension == '.docx':
            with ZipFile(upload) as archive:
                entries = archive.infolist()
                valid = (
                    sum(item.file_size for item in entries) <= 25 * 1024 * 1024
                    and {
                        '[Content_Types].xml',
                        'word/document.xml'
                    }.issubset(archive.namelist())
                    and not any('vbaProject' in item.filename for item in entries)
                )
                if valid:
                    # Read required XML to check ZIP integrity and basic document structure.
                    from xml.etree import ElementTree
                    for name in ('[Content_Types].xml', 'word/document.xml'):
                        ElementTree.fromstring(archive.read(name))
        else:
            valid = False
    except Exception as error:
        # Parsing failures become an understandable form error, never a server error.
        raise ValidationError('This file is not a valid PDF, DOCX, or DOC document.') from error
    finally:
        upload.seek(0)
    if not valid:
        raise ValidationError(
            'Choose a valid PDF, DOCX, or DOC document matching its extension.'
        )
