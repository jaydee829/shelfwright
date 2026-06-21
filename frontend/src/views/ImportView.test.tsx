import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

// Prevent Firebase from throwing auth/invalid-api-key under test (no env keys).
vi.mock('../auth/firebase', () => ({ getIdToken: vi.fn().mockResolvedValue(null) }))

import ImportView from './ImportView'
import * as client from '../api/client'

afterEach(() => vi.restoreAllMocks())

const PREVIEW: client.ImportPreview = {
  source: 'goodreads',
  headers: ['Title', 'Author'],
  suggested_mapping: { title: 'Title', author: 'Author', date_completed: 'Date Read' },
  preview_rows: [{ title: 'Dune', author: 'Frank Herbert', format: 'ebook', date_completed: '2024-03-05', rating: 5, shelf: 'read' }],
  counts: { read_dated: 1, read_undated: 0, to_read: 1, currently_reading: 0, total: 2 },
}

function uploadFile() {
  const input = screen.getByTestId('import-file') as HTMLInputElement
  const file = new File(['Title,Author\nDune,Frank Herbert'], 'export.csv', { type: 'text/csv' })
  fireEvent.change(input, { target: { files: [file] } })
}

describe('ImportView', () => {
  it('previews after upload and advances to mapping', async () => {
    vi.spyOn(client, 'previewImport').mockResolvedValue(PREVIEW)
    render(<ImportView />)
    uploadFile()
    await waitFor(() => expect(screen.getByText(/Detected: goodreads/i)).toBeInTheDocument())
    expect(screen.getByText(/1 read/i)).toBeInTheDocument()
  })

  it('commits and then polls status to completion', async () => {
    vi.spyOn(client, 'previewImport').mockResolvedValue(PREVIEW)
    vi.spyOn(client, 'commitImport').mockResolvedValue({ import_job_id: 'j1', total_rows: 2, enqueued: 2 })
    vi.spyOn(client, 'getImportJob').mockResolvedValue({
      import_job_id: 'j1', source: 'goodreads', total_rows: 2,
      counts: { done: 2 }, outcomes: { linked: 2 }, complete: true, stalled: 0, report: [],
    })
    render(<ImportView />)
    uploadFile()
    await screen.findByText(/Detected: goodreads/i)
    fireEvent.click(screen.getByRole('button', { name: /continue/i }))     // map → review
    fireEvent.click(screen.getByRole('button', { name: /start import/i })) // review → progress
    await waitFor(() => expect(screen.getByText(/2 \/ 2/)).toBeInTheDocument())
  })

  it('offers retry when rows are stalled (not yet complete)', async () => {
    vi.spyOn(client, 'previewImport').mockResolvedValue(PREVIEW)
    vi.spyOn(client, 'commitImport').mockResolvedValue({ import_job_id: 'j1', total_rows: 2, enqueued: 2 })
    vi.spyOn(client, 'getImportJob').mockResolvedValue({
      import_job_id: 'j1', source: 'goodreads', total_rows: 2,
      counts: { processing: 1, done: 1 }, outcomes: {}, complete: false, stalled: 1, report: [],
    })
    vi.spyOn(client, 'retryImport').mockResolvedValue({ retried: 1 })
    render(<ImportView />)
    uploadFile()
    await screen.findByText(/Detected: goodreads/i)
    fireEvent.click(screen.getByRole('button', { name: /continue/i }))
    fireEvent.click(screen.getByRole('button', { name: /start import/i }))
    await waitFor(() => expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument())
  })
})
