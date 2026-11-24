import { execFile } from 'node:child_process';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { extname, join } from 'node:path';
import { promisify } from 'node:util';
import { DomUtils, parseDocument } from 'htmlparser2';

const exec = promisify(execFile);
const parserOptions = { timeout: 12000, maxBuffer: 2 * 1024 * 1024 };
const pageSize = 18000;

export function imageType(data) {
  if (data.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) return 'image/png';
  if (data[0] === 255 && data[1] === 216 && data[2] === 255) return 'image/jpeg';
  if (data.toString('ascii', 0, 4) === 'RIFF' && data.toString('ascii', 8, 12) === 'WEBP') return 'image/webp';
}

function wordText(xml) {
  const document = parseDocument(xml, { xmlMode: true });
  const paragraphs = DomUtils.findAll(node => node.name === 'w:p', document.children);
  return paragraphs.map(paragraph => DomUtils.findAll(node => node.name === 'w:t', paragraph.children)
    .map(node => DomUtils.textContent(node)).join('')).join('\n');
}

async function documentText(data, extension) {
  const directory = await mkdtemp(join(tmpdir(), 'matrix-attachment-'));
  const path = join(directory, 'document' + extension);
  try {
    await writeFile(path, data, { mode: 0o600 });
    if (extension === '.pdf') return (await exec('pdftotext', ['-enc', 'UTF-8', path, '-'], parserOptions)).stdout;
    const { stdout } = await exec('unzip', ['-p', path, 'word/document.xml'], parserOptions);
    return wordText(stdout);
  } catch {
    throw new Error('Text could not be extracted from this document. It may be invalid, protected, or too complex.');
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

export async function inspectAttachment(data, filename, offset = 0) {
  const image = imageType(data);
  if (image) return [{ type: 'image', mimeType: image, data: data.toString('base64') }];
  const ext = extname(filename).toLowerCase();
  let text;
  if (ext === '.pdf' || ext === '.docx') {
    text = await documentText(data, ext);
    if (!text.trim()) throw new Error('No extractable text was found. For a scanned document, provide an image or a text version.');
  } else {
    try {
      text = new TextDecoder('utf-8', { fatal: true }).decode(data);
      if (/[\x00-\x08\x0b\x0e-\x1f]/.test(text)) throw new Error('Binary file');
    } catch {
      throw new Error('This format can be transferred, but cannot be read here. Supported formats are UTF-8 text, PDF text, DOCX text, PNG, JPEG, and WebP.');
    }
  }
  const next = offset + pageSize;
  return [{ type: 'text', text: JSON.stringify({ text: text.slice(offset, next), next_offset: next < text.length ? next : null }) }];
}
