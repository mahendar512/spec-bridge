from email.message import EmailMessage

import docx

import create_samples
from parsing import parse_file, parse_path


def test_txt_utf8_and_cp1252():
    assert parse_file("notes.txt", "Staff can see their balance – always".encode("utf-8")).text \
        == "Staff can see their balance – always"
    doc = parse_file("old.txt", "Caf\xe9 rules".encode("cp1252"))
    assert doc.error is None and doc.text == "Café rules"


def test_docx_paragraphs_and_tables(tmp_path):
    d = docx.Document()
    d.add_paragraph("Staff can carry over a maximum of 5 days.")
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text, t.rows[0].cells[1].text = "Owner", "HR"
    path = tmp_path / "policy.docx"
    d.save(path)
    doc = parse_path(str(path))
    assert doc.error is None
    assert "maximum of 5 days" in doc.text
    assert "Owner | HR" in doc.text


def test_eml_headers_and_body():
    msg = EmailMessage()
    msg["From"] = "Sam <sam@company.com>"
    msg["To"] = "alex@company.com"
    msg["Cc"] = "specbridge+leave-app@company.com"
    msg["Date"] = "Tue, 17 Nov 2026 10:42:00 +0000"
    msg["Subject"] = "Carry over"
    msg.set_content("Staff can carry over up to 10 unused leave days.")
    doc = parse_file("mail.eml", bytes(msg))
    assert doc.kind == "email"
    assert doc.meta["subject"] == "Carry over"
    assert "specbridge+leave-app@company.com" in doc.meta["cc"]
    assert "up to 10 unused leave days" in doc.text


def test_html_only_email_is_converted_to_text():
    msg = EmailMessage()
    msg["Subject"] = "html"
    msg.set_content("<p>Managers should get a <b>reminder</b>.</p>", subtype="html")
    doc = parse_file("mail.eml", bytes(msg))
    assert "Managers should get a reminder" in " ".join(doc.text.split())


def test_bad_files_give_friendly_errors():
    assert "not a supported file type" in parse_file("image.png", b"x").error
    assert "could not be read" in parse_file("broken.docx", b"not a zip").error
    assert "could not be read" in parse_file("broken.pdf", b"not a pdf").error
    assert "no text" in parse_file("empty.txt", b"   ").error


def test_sample_files_parse(tmp_path):
    policy = create_samples.build_policy(tmp_path / "HR_Leave_Policy.docx")
    email_path = create_samples.build_email(tmp_path / "HR_email_carry_over.eml")
    p, e = parse_path(str(policy)), parse_path(str(email_path))
    assert "maximum of 5 unused annual leave days" in p.text
    assert "current leave balance" in p.text
    assert "up to 10 unused leave days" in e.text
    assert "specbridge+leave-app@company.com" in e.meta["cc"]
