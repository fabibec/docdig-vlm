# Notes

## Open Problems
- Ring numbers are usually noted once and then applied to following rows until a different number is written in front of it.
- Allow rearranging of detected row in the rendered view, mention arrow switching the rows around should be respected in prompt
- Make LLM flags international by creating predefined error categories
- Detect errors:
    - User uploads .pdf thats not a PDF
    - User uploads PDF that is not a bird ring list
        - no table (handle in table extractor step)
        - different table (handle in ocr/llm step)
    - print some kind of error message when hovering over the error batch (including translations -> return fixed error codes)
- Handle network errors (and backend not reachable)
- Show cost per page in overview
- Allow deletion of batches
- Being able to preview files selected for upload change their names at upload
- Change the name of batches and file later (via icon)

## Future Ideas
- User Concept (Login, Azure AD to allow SSO?)
- Proper Database (Postgres or any existing SQL)
- Storage upload (Cloud Blob Storage, Nextcloud)
- Select model for batch on upload
- Cost Breakdown (per User, also maybe show per Page) as Admin View
- Use OpenAI Batch API to reduce LLM costs (~50% for a max wait time of 24h)
- Stats View
- Test/Finetune Local OCR Model/LLM
- Add automated tests
