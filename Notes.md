# Notes

## Open Problems
- OP 1: Ring numbers are usually noted once and then applied to following rows until a different number is written in front of it usually only the digit that changes is noted.
- OP 2: Allow rearranging of detected row in the rendered view, mention arrow switching the rows around should be respected in prompt
- OP 3: Make LLM flags international by creating predefined error categories
- OP 4.1: Detect errors:
    - User uploads .pdf thats not a PDF
    - User uploads PDF that is not a bird ring list
        - no table (handle in table extractor step)
        - different table (handle in ocr/llm step)
    - print some kind of error message when hovering over the error batch (including translations -> return fixed error codes)
- OP 4.2: Handle network errors (and backend not reachable)
- OP 5: Show cost per page in overview
- OP 6: Allow deletion of batches
- OP 7.1: Being able to preview files selected for upload change their names at upload
- OP 7.2: Change the name of batches and file later (via icon)
- OP 8: Different Sheet versions (new Versions processed via local OCR? -> spawn 2 different Pipelines)
  - Get the species code from the euring database (periodically?)
  - Let Ovis only detect table structure und numbers -> map to list -> manual postprocessing (does detect numbers somewhat well but the whole format is a bit of)
  - In the end we can cut by table cell and get the digits/letters back
- OP 9:csv-export? instead of md?
- OP 10: Benchmark improvements

## Future Ideas
- User Concept (Login, Azure AD to allow SSO?)
- Proper Database (Postgres or any existing SQL)
- Storage upload (Cloud Blob Storage, Nextcloud with App User?)
- Select model for batch on upload
- Cost Breakdown (per User, also maybe show per Page) as Admin View
- Use OpenAI Batch API to reduce LLM costs (~50% for a max wait time of 24h)
- Stats View
- Test/Finetune Local OCR Model/LLM
- Add automated tests
- Should the results also be added into a different database?
- Logging

## Questions
- What age codes are there and what do they mean:
  - N = 1
  - NF = 1
  - F = 2
  - Fd = 3
  - Fnd = 4
  - Fv = 5
  - Fnv = 6
- Examples
  - ad = 4
  - dj = 3
- What sex to pick in old scans there are sometimes more than 1 or ()
  - unknown (cross) = 0
  - male = 1
  - female = 2
- Do you want all columns for the newer lists as well?
  - coordinates
  - altitude
  - place
  - feather length
  - p ?
  - do these additional remarks carry some extra info?

