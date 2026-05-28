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
- Listing cards show date, title, series, sequence, host, public summary, source count, and authenticated document availability.
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
  - Source links
- Public summary and key learnings are entered and rendered as Markdown.

Document access behavior:

- Fellowship documents are stored under `data/fellowship/docs/[YYYY-MM-DD]`.
- Documents are visible only to authenticated users.
- Non-Markdown documents open through the protected file endpoint.
- Markdown documents (`*.md`) open as rendered web pages instead of raw downloads.
- Unauthenticated users see a login/access notice for protected documents.

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

Learning content generation:

- Key learnings and summary can be generated from associated fellowship documents.
- Generated content can be manually edited in the admin page.
- Summary and key learnings are stored as Markdown text.

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

Important implication:

- File and folder naming conventions are part of the functional contract for modules that link documents to content records.

## Current Functional Gaps

The following areas may need fuller documentation or future decisions:

- Formal role matrix for admin permissions.
- Publication workflow states for generated content.
- Review/approval process for AI-generated resources.
- Data retention policy for contact submissions and email history.
- Public/private rules for every sermon media type.
- Backup and restore expectations for filesystem-based content.

