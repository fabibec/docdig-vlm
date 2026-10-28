- Darkmode based on system preference

- Batches Page
    - The Title of the App should be DocDig and i also want to show the vogelwarten
    - Remove Watched Folder
    - Pagination for both large batch (files) and number of batches (try to fix this to page length somehow)
    - If i click on a file it should bring me to the review queue of that file, if i click on a batch it should bring me to the review queue of that file
    - If i click on a file it should bring me to the review queue of that file, if i click on a batch it should bring me to the review queue of that batch
    - If i click on the review queue it should loop through all batches beginning with the oldest one (if nothing is to do the page should show a green check icon and say no review needed or something)
    - The logo of the Voglewarte should show in the upper left corner
    - Click to file brings the to the review queue use Angular Router to build proper sites / (home) /<file_slug> (detail) /review (/batch/<batch-slug>) (/file/<file-slug)>)
    - Only show Review Queue in the top bar (and dark mode select)
    - only files with done status are clickable

- Review View
    - on Top but a selector of the current file if in batch or all mode, show general statistics here (files_left, flags_left)
    - the other items should then be based per file. Make flagged records scrollable for long lists
    - Add a field additional remarks to the record editor, also record editor comment currently is 2 parts, we only return 1 part,
    - Under 2 Table Detection remove buttons
    - under results show rendered or raw (Markdown) only for now but keep the nice previeew feature where if i type somethin into the record editor it changes in the 3 Result as well
    - App should be used on tablet or pc use space accordingly. Input for the visualations steps 1 and 2 are din a4 imgs (jpeg) steps 3 should result in a markdown table with roughly 50 entires + headers
    - A file is almost always multiple pages. a page selector next to the state toogle button, when clicking on a flagged record switch to the proper flags

- Detail Page (new)
    - this is used when clicking on a file that has nothing to review. You can just look at the 3-step view here

- Upload view
    - Check for duplicated names and paginate previewed uploaded files (10-15)
    - For small files use formatted KB instead of MB

multi language ui (de/fr/en) be?

Also modularize the angular nicely
The icon is under public favicon.ico

API Schema

POST /batches
    Batch Name
    Bunch of pdfs

GET /batches
    All (paginated)

GET /batches/{slug}/status
    Status of files in batch

GET /docs/{slug}
    Document Metadata
        Artifact location
        # Open Flags
        num_pages
        status

GET /docs/{slug}/page/{num}/rows
    Get img coordinates for bounding box

POST /docs/{slug}/flags/{flag_id}/resolve
    Resolve Flag

GET /docs/{slug}/artifacts/{state}/page/{page_num}
    state = raw, cropped, processed
    sends back either img
    or text for step 3

WebSocket Endpoint
    Messages:
        - Number of pending entries
        - State updates per File/Batch

    Pipeline send events to this via pipe

    Document Messages with proper Schema (Pydantic)


table crop save row bounds as metadata

Backend

Use SQLModel + Pydantic Schemas on ALL Responses and internal states
Use Streaming for Files and async as often as possible

underlying server will be granian, also try to embed the frontend into the webapp
pipeline should run in processpool -> state changed communicated via pipe and published through websockets so maybe wee shoudl split batches into signle files and distribute over instances?

Table Extractor should return the two coordinates for bounding boxes per row in order for the review view to be able to highlight a cell

this are big changes plan this first properly we especially need to agree und all the schema stuff
write proposed changes into dcos when we are in agreement you can implement and document throughly but no exessive

Also read the current code first to get a fell of the structure

