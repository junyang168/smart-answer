# Church Site Functional Overview

## Purpose

The Dallas Holy Logos Church website serves two related purposes:

1. Provide public church information for visitors, seekers, and church members.
2. Publish and manage Bible teaching resources, especially content derived from Dr. Wang's sermons, fellowship studies, and AI-assisted study workflows.

The site combines a public-facing church website, a resource library, and authenticated administrative tools for maintaining church content and ministry operations.

## Audiences

- **Visitors and seekers**: Learn about the church, ministries, contact information, giving, and public teaching resources.
- **Church members and attendees**: Review sermons, fellowship learning summaries, Bible study resources, Sunday service information, and authenticated fellowship documents.
- **Content editors**: Maintain sermons, Q&A, articles, fellowship entries, micro-sermons, and generated study material.
- **Church administrators**: Manage users, contacts, fellowship schedules, Sunday service assignments, and email communications.

## Public Website

### Home And Church Information

The public site introduces Dallas Holy Logos Church and routes users to core church areas.

Functional areas:

- Home page
- About the church
- Pastor profile
- Ministries
- Contact form
- Giving information
- Special event pages such as Good Friday

Expected behavior:

- Public users can browse church information without login.
- Contact submissions are captured for admin review.
- Giving information is informational and publicly accessible.

## Resource Center

The resource center is the primary public entry point for AI-assisted Bible study material and sermon-derived content.

Main route:

- `/resources`

Functional goal:

- Help users discover, search, and study sermon and Bible study resources.
- Present resources in a way that supports both regular church attendees and new visitors.

Current resource modules:

- Sermon Center
- Sermon Series
- 王守仁教授聖經講論文庫
- Full Articles
- Fellowship Study Reviews
- Q&A
- Notes-to-Manuscript Series
- Micro-Sermons
- Depth of Faith audio program

## Sermon Center

Routes:

- `/resources/sermons`
- `/resources/sermons/[id]`
- `/resources/series`
- `/resources/series/[seriesId]`

Functional behavior:

- Public users can browse sermons and sermon series.
- Sermon detail pages show title, speaker, date, media availability, summary, and study content.
- Authenticated users may receive access to additional media or full content depending on the sermon module's access rules.
- Sermon content is AI-generated and/or editor-reviewed before publication.
- Editors see a right-rail list of every passage and topic repository unit sourced from the current sermon, including unpublished candidates; public readers do not see unpublished unit metadata.
- Citation deep links highlight the relevant transcript excerpt and position the sermon audio or video at the corresponding time.

## Wang Exegesis And Topic Repository

Routes:

- `/resources/wang-repository`
- `/resources/wang-repository/[unitId]`
- `/admin/canonical-repository`
- `/admin/canonical-repository/[unitId]`

Functional behavior:

- Public readers browse the same reviewed units through a Bible index or a topic index.
- The Bible index groups units by canonical book and chapter and sorts them by verse; a unit with multiple references appears once in the appropriate grouping.
- The topic index groups concept units by reviewed taxonomy paths and does not duplicate passage units merely because they carry topic metadata.
- Each public unit exposes approved original-source excerpts. Sermon sources include an embedded audio/video player positioned at the citation time and links that open the complete sermon in a new tab.
- The public manuscript body is temporarily hidden while the editorial presentation is reviewed; manuscript content remains visible in the admin review page.
- Editors review manuscript Markdown, source excerpts, media positioning, citation status, Bible references, topic paths, and publication state.
- Pure Markdown headings are not accepted as source evidence. Existing heading-only source links can be detached without deleting their underlying audit records.
- The long-term repository is a shared knowledge platform, not only an article catalog. Reviewed questions, claims, argument relations, Scripture evidence, original-language judgments, applications, and versioned thought-map decisions can drive passage lectures, topic essays, intelligent QA, search, comparison, and study tools.
- Articles are publication projections of reviewed knowledge. They are not the sole machine-readable record of the professor's teaching.
- Intelligent QA must distinguish the professor's explicit claims, reasoning conclusions, opposed views, editorial synthesis, pending fact checks, and insufficient evidence, and must resolve every public answer to approved exact sources.
- Detailed specifications live in:
  - `docs/wang-knowledge-platform/README.md`
  - `docs/wang-knowledge-platform/00-overview/project_mission_statement.md`
  - `docs/wang-knowledge-platform/00-overview/knowledge_platform_design.md`
  - `docs/wang-knowledge-platform/20-knowledge/exegesis_topic_repository_functional_spec.md`
  - `docs/wang-knowledge-platform/20-knowledge/repository-tech-spec/`
  - `docs/wang-knowledge-platform/40-qa-search/sermon_search_functional_spec.md`

## Full Articles

Routes:

- `/resources/full_article`
- `/resources/full_article/[articleId]`

Functional behavior:

- Public users can browse long-form article content generated or organized from sermon material.
- Article detail pages display structured article content and related sermon/source references where available.
- Admin users can manage article content in the backend.

## Fellowship Study Reviews

Routes:

- `/resources/fellowship`
- `/resources/fellowship/[date]`
- `/resources/fellowship/[date]/docs/[...documentPath]`

Functional goal:

- Allow church attendees to review key learning after each fellowship.
- Help people outside the church understand the fellowship's Bible study focus and become interested in attending.

Public listing behavior:

- Only fellowship entries dated today or earlier are shown.
- Future fellowship entries are hidden from the public listing.
- Listing cards show date, title, series, sequence, host, public summary, source count, and public document availability.
- Listing cards do not show learning-point previews.

Detail page behavior:

- Each fellowship has a detail page.
- Public users can view:
  - Date
  - Topic/title
  - Series
  - Sequence
  - Host
  - Public summary
  - Key learnings
  - Audience questions
  - Audience sharing
  - Leader responses
  - Source links
- Public summary, key learnings, audience questions, audience sharing, and leader responses are entered and rendered as Markdown.

Document access behavior:

- Fellowship documents are stored under `FELLOWSHIP_DOCS_DIR/[YYYY-MM-DD]` when configured, otherwise `data/fellowship/docs/[YYYY-MM-DD]`.
- The dated local docs folder is the source of truth for public fellowship document downloads.
- Public document access is allowlisted, not authenticated.
- Public input documents are:
  - Prepared manuscript/teaching notes in Markdown (`*.md`)
  - Fellowship presentation files (`*.pptx`)
  - Local copies of the Google Meet recording MP4 files (`*.mp4`)
- Generated or temporary files are hidden from public document lists and public file endpoints, including:
  - `主題與查經重點.md`
  - `recording.transcript.generated.md`
  - Google Meet chat files
  - Extracted audio such as `audio/*.mp3`
  - Temporary/cache folders
- Markdown documents (`*.md`) open as rendered web pages instead of raw downloads.
- The Markdown document page renders server-side and reads public Markdown from the fellowship docs directory first. It falls back to the backend public text endpoint only when the docs directory is unavailable to the Next.js process.
- Non-Markdown public documents use `/api/fellowship-documents/[date]/[documentPath]`, a Next.js local file route that reads from the same fellowship docs directory.
- PPTX and MP4 links are normal attachment downloads, not new-tab rendered pages. The local file route must preserve `Range` headers for large MP4 downloads.

Admin-managed fellowship fields:

- Date
- Host
- Topic/title
- Series
- Sequence
- Source links
- Public summary
- Key learnings
- Email content
- Associated document links

Source link policy:

- `Source links` / `來源連結` are for teaching/source material links, such as Dr. Wang notes or related reference documents.
- Do not store the global Google Meet Recordings folder in `來源連結`.
- Google Meet recording discovery uses backend configuration such as `FELLOWSHIP_MEET_RECORDINGS_FOLDER_ID` and Drive access, not per-entry public source links.
- When a recording should be publicly downloadable, copy or download the selected MP4 into the fellowship's dated docs folder and let the public document allowlist expose it as a normal input file.

Learning content generation:

- Key learnings and summary can be generated from associated fellowship documents.
- Generated analysis can also populate audience questions, audience sharing, and leader responses.
- Generated content can be manually edited in the admin page.
- Summary, key learnings, and interaction sections are stored as Markdown text.

## Faith Q&A

Routes:

- `/resources/qa`

Functional behavior:

- Public users can browse faith-related questions and answers.
- Q&A content is maintained through the admin area.
- Content is intended to reflect real fellowship or church questions, with editorial cleanup before publication.

## Notes-To-Manuscript Series

Routes:

- `/resources/notes_to_manuscript_series`
- Admin routes under `/admin/notes-to-sermon`

Functional behavior:

- Authenticated users can browse generated manuscript series.
- Admin/editor workflows support transforming notes into structured manuscript drafts.
- The detailed functional and technical specs for this subsystem live in:
  - `docs/notes-to-sermon-agent/functional_spec.md`
  - `docs/notes-to-sermon-agent/tech_spec.md`

## Micro-Sermons

Routes:

- `/resources/micro-sermon`
- `/admin/micro-sermon`

Functional behavior:

- Public users can browse short-form teaching videos.
- Admin users can manage video titles, series, YouTube links, and descriptions.
- The existing public and admin routes are the intended delivery surface for the Wang knowledge platform's three-to-five-minute teaching use case; a separate micro-sermon site is not required.
- Current records are delivery metadata only. Target knowledge-managed records link one central question and a minimum complete argument to an approved Composition Plan, active knowledge snapshot, claims, relations, exact citations, and ProductDependencies.
- Two source modes are supported by the target design: an exact Dr. Wang audio/video excerpt with transcript context, and a clearly attributed editorial synthesis assembled from reviewed claims across one or more sermons.
- A micro-sermon is not a mechanically truncated manuscript. It must preserve material premises and qualifications, link to the complete sermon and deeper exegesis/topic material, and return to review when an upstream dependency becomes stale or invalidated.
- Full design and rollout details are documented in `docs/wang-knowledge-platform/50-micro-sermon/micro_sermon_product_use_case.md`.

## Depth Of Faith

Routes:

- `/resources/depth_of_faith`

Functional behavior:

- Public users can listen to audio teaching episodes.
- Admin users maintain episode metadata, audio, scripture references, and summaries.

## Admin Area

Main route:

- `/admin`

Functional goal:

- Provide authenticated church editors and administrators with tools for maintaining site content and ministry operations.

Admin modules:

- Full article editor
- Q&A editor
- Fellowship management
- Sunday service management
- Sunday worker management
- Sermon series management
- Sermon management
- Depth of Faith program management
- General email sender
- Notes-to-sermon workflow
- Contact submissions
- User management
- Micro-sermon management

Access expectations:

- Admin pages require authenticated users.
- Some functions are intended for administrators only.
- User and permission management is handled through the admin user module.

## Sunday Service Management

Routes:

- `/admin/sunday-service`
- `/admin/sunday-workers`

Functional behavior:

- Admin users manage Sunday service dates, workers, songs, scripture readings, announcements, and email content.
- Sunday worker data supports assignment and availability workflows.
- Generated or prepared materials may be exported or emailed to church members.

## Contact Management

Routes:

- Public: `/contact`
- Admin: `/admin/contacts`

Functional behavior:

- Visitors submit contact requests through the public form.
- Admin users review submitted contact information and messages.

## Email Tools

Routes:

- `/admin/email`
- Fellowship email tools within `/admin/fellowship`
- Sunday service email tools within `/admin/sunday-service`

Functional behavior:

- Authorized users can send HTML email to church recipients.
- Fellowship and Sunday service modules can prepare module-specific email content.

## Authentication And Authorization

Authentication is based on Google sign-in through NextAuth.

Public without login:

- Church information pages
- Public resource listing and detail pages
- Public sermon/article/Q&A/fellowship summaries

Authenticated:

- Protected sermon/media content where applicable
- Fellowship documents
- Notes-to-manuscript resources
- Admin area

Admin/editor:

- Content management
- User management
- Email sending
- Fellowship and Sunday service operations

## Data Ownership And Storage

Primary storage pattern:

- Structured metadata is stored in JSON/config files under the configured data directory.
- Generated Markdown, sermon resources, fellowship documents, slides, and media are stored on the filesystem.
- Fellowship documents are organized by ISO date folder:
  - `data/fellowship/docs/YYYY-MM-DD`
- The Next.js process must be able to read the fellowship docs directory for server-rendered public Markdown pages:
  - Prefer `FELLOWSHIP_DOCS_DIR` when set.
  - Otherwise use `DATA_BASE_DIR/fellowship/docs`.
  - The production deployment should provide the same filesystem path to both the FastAPI backend and the Next.js frontend process.

Important implication:

- File and folder naming conventions are part of the functional contract for modules that link documents to content records.
- Data setup should be corrected in the docs folder when public input files are missing. Avoid hard-coding fellowship-specific filenames or source links in code.
- Public fellowship document visibility must remain consistent between:
  - Backend allowlist logic (`list_public_fellowship_documents` / public document endpoint)
  - Frontend document links
  - Server-rendered Markdown file access

## Current Functional Gaps

The following areas may need fuller documentation or future decisions:

- Formal role matrix for admin permissions.
- Publication workflow states for generated content.
- Review/approval process for AI-generated resources.
- Data retention policy for contact submissions and email history.
- Public/private rules for every sermon media type.
- Backup and restore expectations for filesystem-based content.
