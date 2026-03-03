# PDF Form Filler

A Python web application that lets you load PDF files, add text annotations, and draw/place signatures to fill PDF forms. Runs in your browser.

## Features

- **Load any PDF** – Drag & drop or browse to upload PDF documents
- **Navigate pages** – Browse through all pages of a PDF
- **Add text** – Click anywhere on the PDF to place text with custom font size
- **Draw signatures** – Use the built-in signature pad to hand-draw a signature
- **Place signatures** – Click on the PDF to position your signature
- **Undo** – Remove the last annotation on any page
- **Save filled PDF** – Download the annotated PDF

## Installation

```bash
# Install dependencies with pipenv
pipenv install
```

Requires [poppler](https://poppler.freedesktop.org/) for PDF rendering:

```bash
# macOS
brew install poppler
```

## Usage

```bash
pipenv run python main.py
```

The app opens automatically at **http://127.0.0.1:5000**

### Workflow

1. Drag & drop or browse to upload a PDF
2. Click **✏️ Add Text**, then click on the PDF → type your text
3. Click **🖊️ Draw Signature** to draw your signature on the pad
4. Click **📌 Place Signature**, then click on the PDF to position it
5. Use **◀ Prev** / **Next ▶** to navigate pages
6. Click **💾 Save PDF** to download the filled PDF

## Project Structure

```
PDF/
├── main.py              # Entry point – launches Flask server
├── app.py               # Flask routes and annotation overlay logic
├── pdf_engine.py        # PDF loading, rendering, and annotation engine
├── templates/
│   ├── upload.html      # Upload page with drag & drop
│   └── editor.html      # PDF editor with toolbar & signature pad
├── Pipfile              # Pipenv dependencies
├── Pipfile.lock         # Locked dependency versions
└── README.md            # This file
```

## Tech Stack

- **Flask** – Web framework
- **pypdf** – PDF reading and writing
- **ReportLab** – PDF text and image overlay generation
- **pdf2image** + **poppler** – PDF page rendering to images
- **Pillow** – Image processing
