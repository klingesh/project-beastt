"""Attaching an image to an assistant that cannot see.

Asked for: images alongside the PDFs and Word documents that already worked, and
pasting rather than only using the attach button.

The second half is easy. The first half contains a trap, and the trap is the point
of most of this file. `llama3.1:8b` is not multimodal and `Message.content` is a
plain string -- there is no path through the brain layer for an image at all. So an
image that were simply accepted and filed would have its next question answered
from the *filename*, fluently and wrongly. That is the Casagrand failure again:
sourced-looking, and about the wrong thing.

So what an image contributes is bounded and stated: what the file is, any text OCR
can read out of it, and plainly that the picture itself has not been seen.
"""

from __future__ import annotations

import re

import pytest

from beastt import imageread

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 40
GIF = b"GIF89a" + b"\x00" * 40
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 30
BMP = b"BM" + b"\x00" * 40


class TestWhatCountsAsAnImage:
    @pytest.mark.parametrize("name", [
        "shot.png", "photo.JPG", "a.jpeg", "loop.gif", "small.webp", "old.bmp",
        "pasted-2026-08-14-19-02-11.png",
    ])
    def test_image_names(self, name):
        assert imageread.is_image(name) is True

    @pytest.mark.parametrize("name", [
        "report.pdf", "notes.docx", "deck.pptx", "data.csv", "code.py",
        "noextension", "", "image", "photo.svg", "clip.mp4",
    ])
    def test_everything_else(self, name):
        assert imageread.is_image(name) is False


class TestSniff:
    @pytest.mark.parametrize("data,expected", [
        (PNG, "PNG"), (JPEG, "JPEG"), (GIF, "GIF"), (WEBP, "WEBP"), (BMP, "BMP"),
    ])
    def test_formats_are_read_from_the_bytes(self, data, expected):
        assert imageread.sniff(data) == expected

    @pytest.mark.parametrize("data", [
        b"%PDF-1.4 not an image", b"<html></html>", b"", b"MZ\x90\x00", None,
    ])
    def test_non_images_are_rejected(self, data):
        assert imageread.sniff(data) == ""

    def test_the_extension_is_not_trusted(self):
        """A .png containing something else is not a picture, whatever it is
        called -- the same reasoning as the download check in images.py."""
        assert imageread.is_image("payload.png") is True
        assert imageread.sniff(b"MZ\x90\x00 this is an executable") == ""

    def test_a_truncated_signature_is_not_enough(self):
        assert imageread.sniff(b"\x89PNG") == ""


class TestSummarise:
    def test_it_names_the_format_and_size(self):
        note = imageread.summarise("shot.png", PNG)
        assert "PNG" in note
        assert "bytes" in note

    def test_a_large_file_reads_in_kb(self):
        assert "KB" in imageread.summarise("big.png", PNG + b"\x00" * 90_000)

    def test_it_falls_back_to_the_extension_when_bytes_are_unknown(self):
        assert "WEBP" in imageread.summarise("x.webp", b"nonsense bytes here")


class TestOcr:
    """Optional, and its absence is a normal state rather than an error --
    pytesseract also needs the Tesseract program, which is a real install."""

    def test_it_never_raises(self):
        text, why = imageread.ocr(PNG)
        assert isinstance(text, str)
        assert isinstance(why, str)

    def test_a_missing_library_is_explained_not_silent(self):
        """An empty result with no reason reads as "there was no text in the
        image", which is a different statement from "nothing here can read
        text"."""
        text, why = imageread.ocr(PNG)
        if not text:
            assert why, "an empty OCR result must say why it is empty"
            assert "install" in why.lower() or "failed" in why.lower()

    def test_junk_bytes_do_not_raise(self):
        assert imageread.ocr(b"not an image at all") is not None

    @pytest.fixture
    def fake_pillow(self, monkeypatch):
        """Pretend Pillow is installed.

        Needed because it isn't, here or on a bare install -- so the Pillow branch
        returns first and every later branch is unreachable. Without this, the
        pytesseract and OCR-failure paths could both be deleted with the suite
        still green.
        """
        import sys
        import types

        pil = types.ModuleType("PIL")
        image_module = types.ModuleType("PIL.Image")

        class _Ctx:
            width, height = 100, 50

            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

        image_module.open = lambda _stream: _Ctx()
        pil.Image = image_module
        monkeypatch.setitem(sys.modules, "PIL", pil)
        monkeypatch.setitem(sys.modules, "PIL.Image", image_module)
        return image_module

    def test_a_missing_ocr_package_names_what_to_install(self, fake_pillow,
                                                        monkeypatch):
        """Distinct from Pillow being absent: different problem, different fix."""
        import sys

        monkeypatch.setitem(sys.modules, "pytesseract", None)

        text, why = imageread.ocr(PNG)

        assert text == ""
        assert "pytesseract" in why
        assert "Tesseract" in why

    def test_a_broken_ocr_binary_is_reported_not_raised(self, fake_pillow,
                                                       monkeypatch):
        """The commonest real failure: the Python package installed and the
        Tesseract program missing."""
        import sys
        import types

        broken = types.ModuleType("pytesseract")

        def explode(_picture):
            raise RuntimeError("tesseract is not installed or it's not in PATH")

        broken.image_to_string = explode
        monkeypatch.setitem(sys.modules, "pytesseract", broken)

        text, why = imageread.ocr(PNG)

        assert text == ""
        assert "failed" in why

    def test_text_is_returned_when_ocr_works(self, fake_pillow, monkeypatch):
        import sys
        import types

        working = types.ModuleType("pytesseract")
        working.image_to_string = lambda _p: "Top Gainers\n\n  Astral Ltd 8.74%  \n"
        monkeypatch.setitem(sys.modules, "pytesseract", working)

        text, why = imageread.ocr(PNG)

        assert why == ""
        # Trailing space and blank lines go; leading indentation stays, because in
        # a screenshot of a table it is the only layout signal OCR leaves behind.
        assert text == "Top Gainers\n  Astral Ltd 8.74%"
        assert "\n\n" not in text
        assert not text.endswith(" ")

    def test_dimensions_are_read_when_pillow_is_present(self, fake_pillow):
        assert imageread.dimensions(PNG) == (100, 50)
        assert "100x50" in imageread.summarise("shot.png", PNG)


class TestWhatTheModelIsTold:
    """The heart of it. Everything here is framed so that describing the picture
    is not an option."""

    def _text(self, name="shot.png", data=PNG, user="Lingaa"):
        text, _note, _trunc = imageread.read(name, data, user)
        return text

    def test_it_says_the_model_cannot_see(self):
        assert "cannot see images" in self._text()

    def test_it_forbids_describing_the_picture(self):
        text = self._text()
        assert "Do not describe it" in text
        assert "guess at its appearance" in text

    def test_it_forbids_reading_anything_into_the_filename(self):
        """The specific failure mode: a file called "sales-chart-q3.png" invites
        a confident paragraph about Q3 sales."""
        assert "infer anything from its filename" in self._text()

    def test_it_names_why_that_is_worse_than_admitting_the_limit(self):
        assert "indistinguishable from having seen it" in self._text()

    def test_it_identifies_the_file(self):
        text = self._text(name="screenshot.png")
        assert "screenshot.png" in text
        assert "PNG" in text

    def test_with_no_text_it_asks_rather_than_guesses(self):
        text = self._text()
        assert "nothing about what it shows" in text
        assert "ask Lingaa what is in it" in text

    def test_it_uses_the_users_name(self):
        assert "Priya" in self._text(user="Priya")

    def test_ocr_text_is_included_and_labelled_approximate(self, monkeypatch):
        monkeypatch.setattr(imageread, "ocr",
                            lambda _data: ("Top Gainers\nAstral Ltd 8.74%", ""))

        text, note, truncated = imageread.read("shot.png", PNG, "Lingaa")

        assert "Astral Ltd 8.74%" in text
        assert "recognition errors" in text
        assert "no layout" in text
        assert "text read" in note
        assert truncated is False

    def test_long_ocr_text_is_truncated_and_says_so(self, monkeypatch):
        monkeypatch.setattr(imageread, "ocr",
                            lambda _data: ("word " * 4000, ""))

        text, _note, truncated = imageread.read("shot.png", PNG, "Lingaa")

        assert truncated is True
        assert "only the first part" in text
        assert len(text) < imageread.MAX_OCR_CHARS + 1200

    def test_the_note_shown_to_the_user_is_short(self):
        _text, note, _trunc = imageread.read("shot.png", PNG, "Lingaa")
        assert len(note) < 60
        assert note.startswith("image")


class TestStorage:
    def test_an_image_keeps_its_bytes_as_well_as_its_text(self, tmp_path,
                                                          monkeypatch):
        """Documents only need their text. An image has to be shown back."""
        from beastt.webui import chats

        monkeypatch.setattr(chats, "data_dir", lambda: tmp_path)
        chat = chats.create()

        entry = chats.add_attachment(chat, "shot.png", "image · PNG", "some text",
                                     blob=PNG, kind="image")

        assert entry["kind"] == "image"
        assert entry["file"]
        stored = chats.uploads_dir(chat["id"]) / entry["file"]
        assert stored.read_bytes() == PNG

    def test_a_document_is_unchanged(self, tmp_path, monkeypatch):
        from beastt.webui import chats

        monkeypatch.setattr(chats, "data_dir", lambda: tmp_path)
        chat = chats.create()

        entry = chats.add_attachment(chat, "report.pdf", "12 pages", "the text")

        assert entry["kind"] == "document"
        assert "file" not in entry

    def test_the_stored_name_is_sanitised(self, tmp_path, monkeypatch):
        """The name arrives from a request."""
        from beastt.webui import chats

        monkeypatch.setattr(chats, "data_dir", lambda: tmp_path)
        chat = chats.create()

        entry = chats.add_attachment(chat, "../../evil name.png", "n", "t",
                                     blob=PNG, kind="image")

        # Dots survive (they are legitimate in a filename) but every separator
        # is replaced, which is what makes traversal impossible: "a/../b" cannot
        # be reassembled out of a name with no slashes in it.
        assert "/" not in entry["file"]
        assert "\\" not in entry["file"]
        stored = chats.uploads_dir(chat["id"]) / entry["file"]
        assert stored.is_file()
        assert stored.resolve().parent == chats.uploads_dir(chat["id"]).resolve()

    def test_a_name_sanitising_to_a_dotfile_is_stored_but_not_served(self,
                                                                    tmp_path,
                                                                    monkeypatch):
        """A known, deliberate corner. `_attachment` refuses anything beginning
        with a dot as a second guard, so a file called "..odd.png" is kept but its
        thumbnail 404s. Recorded rather than fixed: the guard is worth more than
        the thumbnail, and the alternative is loosening a path check to make an
        unusual filename look prettier."""
        from beastt.webui import chats

        monkeypatch.setattr(chats, "data_dir", lambda: tmp_path)
        chat = chats.create()

        entry = chats.add_attachment(chat, "..odd.png", "n", "t", blob=PNG,
                                     kind="image")

        assert entry["file"].startswith(".")
        assert (chats.uploads_dir(chat["id"]) / entry["file"]).is_file()

        # And the route refuses it, which is the half that has to be asserted --
        # storing it proves nothing about whether the dot guard still exists.
        from beastt.webui import server

        handler = object.__new__(server.Handler)
        replies = []
        handler._json = lambda payload, code=200: replies.append(code)
        handler._send = lambda *_a: replies.append(200)

        handler._attachment(f"{chat['id']}/{entry['file']}")

        assert replies == [400]

    @pytest.mark.parametrize("name", [".", "..", "...", "....", "/", "//"])
    def test_a_name_that_is_only_dots_or_slashes_does_not_crash(self, tmp_path,
                                                               monkeypatch,
                                                               name):
        """Found while checking which guard on the serving route was load-bearing.

        A name of nothing but dots survives the character substitution and then
        names a *directory*: "." is the uploads folder and ".." is its parent, so
        writing to either raised IsADirectoryError straight out of the upload
        handler. Not a way out of the folder -- it fails rather than escaping --
        but an unhandled crash on a one-character filename.
        """
        from beastt.webui import chats

        monkeypatch.setattr(chats, "data_dir", lambda: tmp_path)
        chat = chats.create()

        entry = chats.add_attachment(chat, name, "n", "t", blob=PNG, kind="image")

        stored = chats.uploads_dir(chat["id"]) / entry["file"]
        assert stored.is_file()
        assert stored.read_bytes() == PNG

    def test_re_attaching_replaces_rather_than_duplicating(self, tmp_path,
                                                          monkeypatch):
        from beastt.webui import chats

        monkeypatch.setattr(chats, "data_dir", lambda: tmp_path)
        chat = chats.create()

        chats.add_attachment(chat, "shot.png", "n", "t", blob=PNG, kind="image")
        chats.add_attachment(chat, "shot.png", "n", "t2", blob=PNG, kind="image")

        assert len(chat["attachments"]) == 1


class TestServingItBack:
    """The route that shows the thumbnail. The path arrives from a request, and
    the folder it reads from also holds the extracted text of every document ever
    uploaded to that chat."""

    @pytest.fixture
    def handler(self, tmp_path, monkeypatch):
        from beastt.webui import chats, server

        monkeypatch.setattr(chats, "data_dir", lambda: tmp_path)

        instance = object.__new__(server.Handler)
        instance.sent = []
        instance.json_replies = []
        instance._json = lambda payload, code=200: instance.json_replies.append(
            (code, payload))
        instance._send = lambda code, body, kind: instance.sent.append(
            (code, body, kind))
        return instance

    def _chat_with_image(self, name="shot.png", data=PNG):
        from beastt.webui import chats

        chat = chats.create()
        entry = chats.add_attachment(chat, name, "image · PNG", "text",
                                     blob=data, kind="image")
        return chat, entry

    def test_a_recorded_image_is_served(self, handler):
        chat, entry = self._chat_with_image()

        handler._attachment(f"{chat['id']}/{entry['file']}")

        assert handler.sent
        code, body, kind = handler.sent[0]
        assert code == 200
        assert body == PNG
        assert kind.startswith("image/")

    @pytest.mark.parametrize("path", [
        "../../../etc/passwd",
        "abc/../../secret.png",
        "abc/..%2Fsecret.png",
        "onlyonepart",
        "",
        "a/b/c",
        "abc/.hidden",
    ])
    def test_traversal_and_nonsense_are_refused(self, handler, path):
        handler._attachment(path)

        assert handler.sent == []
        assert handler.json_replies
        assert handler.json_replies[0][0] in (400, 404)

    def test_a_file_not_recorded_as_an_attachment_is_not_served(self, handler,
                                                               tmp_path):
        """The folder also holds every document's extracted text. This route has
        no business handing those out by guessing at names."""
        from beastt.webui import chats

        chat, _entry = self._chat_with_image()
        secret = chats.uploads_dir(chat["id"]) / "report.pdf.txt"
        secret.write_text("confidential contents", encoding="utf-8")

        handler._attachment(f"{chat['id']}/report.pdf.txt")

        assert handler.sent == []
        assert handler.json_replies[0][0] == 404

    def test_a_non_image_attachment_is_refused_even_if_recorded(self, handler):
        """This route exists to show pictures."""
        chat, entry = self._chat_with_image(name="notes.txt", data=b"plain text")

        handler._attachment(f"{chat['id']}/{entry['file']}")

        assert handler.sent == []
        assert handler.json_replies[0][0] == 400

    def test_an_unknown_chat_is_refused(self, handler):
        handler._attachment("deadbeef/shot.png")

        assert handler.sent == []
        assert handler.json_replies[0][0] == 404

    @pytest.mark.parametrize("hostile", [
        "../../../etc/passwd", "..%2F..%2Fetc%2Fpasswd", "a/../../../secret",
        "...././shot.png", "%2e%2e/%2e%2e/shot.png", "//etc/passwd",
        "abc/shot.png%00.txt", "abc/....//shot.png", " / ", "abc/",
        "abc/CON", "abc/shot.png.exe",
    ])
    def test_nothing_outside_the_uploads_folder_is_ever_served(self, handler,
                                                              tmp_path, hostile):
        """The invariant, asserted as one property over a battery of inputs.

        The individual guards on this route are deliberately redundant -- the path
        is sanitised, the name is checked against the chat's own recorded
        attachments, *and* the resolved path is checked against the folder. Each
        one alone would stop everything here, so removing any single one changes
        no observable behaviour and no test can isolate it. That is the intended
        design ("one guard on a file server is not enough"), and this is the thing
        actually worth holding: whatever arrives, nothing outside that chat's
        uploads folder comes back.
        """
        self._chat_with_image()

        handler._attachment(hostile)

        for code, body, _kind in handler.sent:
            assert body == PNG, f"{hostile!r} served unexpected bytes"
        assert handler.json_replies or handler.sent, "the route must answer"

    def test_a_sibling_chats_image_is_not_reachable(self, handler):
        """The chat id in the path has to match the chat the file belongs to."""
        _mine, _entry = self._chat_with_image()
        theirs, their_file = self._chat_with_image(name="private.png")

        handler._attachment(f"{_mine['id']}/{their_file['file']}")

        # Same sanitised stem, different chat -- so this may legitimately resolve
        # or 404, but it must never return the other chat's bytes from the other
        # chat's folder.
        for _code, body, _kind in handler.sent:
            assert body == PNG


class TestTheUploadPathAcceptsImages:
    def test_images_are_in_the_accepted_set(self):
        """The gate used to be readers.supported() alone, which rejected every
        image outright."""
        import inspect

        from beastt.webui import server

        source = inspect.getsource(server.Handler._upload)
        assert "imageread.is_image(name)" in source
        assert "not picture and not supported(name)" in source

    def test_the_bytes_are_checked_before_being_stored(self):
        import inspect

        from beastt.webui import server

        source = inspect.getsource(server.Handler._upload)
        assert "imageread.sniff(data)" in source

    def test_the_error_message_now_mentions_images(self):
        import inspect

        from beastt.webui import server

        assert "images (PNG, JPEG" in inspect.getsource(server.Handler._upload)


class TestTheFrontend:
    """Asserted against the file, since there is no browser here. Crude, but the
    paste handler is the requested feature and an unpinned one has gone missing
    three times in this project."""

    @pytest.fixture
    def app_js(self):
        from pathlib import Path

        return (Path("beastt/webui/static/app.js")).read_text(encoding="utf-8")

    def test_pasting_is_handled(self, app_js):
        assert 'addEventListener("paste"' in app_js
        assert "clipboardData" in app_js

    def test_pasting_text_still_works(self, app_js):
        """clipboardData carries both text and files, so intercepting the wrong
        one would swallow every ordinary paste."""
        assert "if (!items.length) return;" in app_js

    def test_a_pasted_image_gets_a_distinct_name(self, app_js):
        """Browsers call every pasted screenshot "image.png", and the server
        de-duplicates attachments by name -- so three pastes would look identical
        and each would replace the last."""
        assert "namePasted" in app_js
        assert 'file.name === "image.png"' in app_js

    def test_images_get_a_thumbnail(self, app_js):
        assert "ac-thumb" in app_js
        assert "/api/attachment/" in app_js
        # The condition, not just the markup. Replacing it with `false` leaves
        # every string above intact, so asserting the class name alone proves
        # nothing about whether a thumbnail is ever produced.
        assert 'file.kind === "image" && file.file && chatId' in app_js

    def test_the_thumbnail_src_is_built_from_the_servers_own_name(self, app_js):
        """Never from anything the uploaded file claimed to be called."""
        assert "encodeURIComponent(file.file)" in app_js

    def test_the_file_picker_offers_images(self):
        from pathlib import Path

        html = Path("beastt/webui/static/index.html").read_text(encoding="utf-8")
        for suffix in (".png", ".jpg", ".gif", ".webp"):
            assert suffix in html

    def test_the_button_mentions_pasting(self):
        from pathlib import Path

        html = Path("beastt/webui/static/index.html").read_text(encoding="utf-8")
        assert "paste" in html.lower()

    def test_the_thumbnail_is_styled(self):
        from pathlib import Path

        css = Path("beastt/webui/static/style.css").read_text(encoding="utf-8")
        # The rule, not the substring: ".ac-thumb" is also inside
        # ".ac-thumb-anything", so a renamed-away rule would still match.
        assert re.search(r"\.ac-thumb\s*\{", css)
        assert re.search(r"\.attach-chip\.is-image\s*\{", css)
