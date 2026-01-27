# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Synapse is a serverless middleware that captures natural-language text and routes it to Notion databases. It uses AI (Gemini 2.5 Flash) to parse, classify, and extract structured data from unstructured input.

## Architecture

```
HTTP Request → intaker (Cloud Function) → Pub/Sub → processor (Cloud Function) → Notion API
                                                                              ↳ External APIs (Spotify, YouTube, TMDB, Google Maps)
```

**Three services in a uv monorepo:**
- `services/intaker/` - HTTP endpoint that validates and publishes to Pub/Sub
- `services/reporter/` - Cron-triggered email summary generator
- `workers/processor/` - Main AI processing worker (handles parsing, classification, extraction, Notion writes)

**The processor pipeline:**
1. Parse raw input (split by `@` delimiter, extract `$` context)
2. Classify intent using Gemini with dynamic project context
3. Extract structured fields based on database schema
4. Execute business logic and write to Notion

## Build & Development Commands

```bash
# Install dependencies
just sync

# Run processor locally with debug logging
just run-processor-debug

# Run processor locally (production mode)
just run-processor

# Send batch requests from local_requests.txt
just recept-local-batch

# Send single request to deployed API
just recept "your text here"

# Add package to specific service
uv add --package processor <package-name>
```

**Local testing:** Add the `syn-local` shell function from README.md to send Cloud Event-formatted requests to `localhost:8080`.

## Key Configuration Files

- `config.yaml` - GCP project settings, secret names, email addresses
- `workers/processor/databases.yaml` - All 25+ Notion database schemas with AI extraction rules
- `workers/processor/prompts.yaml` - Gemini system prompts for parsing/classification/extraction

## Configuration-Driven AI Behavior

The processor is entirely YAML-driven. To add a new Notion database category:

1. Add secret `notion-<category>-db-id` to GCP Secret Manager
2. Add category definition to `databases.yaml` with:
   - `description` - Used by AI for classification decisions
   - `properties` - Field mappings with `type`, `required`, `instruction`, `allowlist`, `virtual`, `create_new`

Property field meanings:
- `instruction` - Extraction prompt (supports `{current_date}`, `{raw_text}` placeholders)
- `virtual: true` - Hidden from AI, populated by Python code only
- `allowlist` - Strict enum values for select/multi_select/status
- `create_new: true` - Allows AI to create new values beyond allowlist

## Code Organization (processor/)

- `main.py` - Entry point, orchestrates the pipeline
- `ai_engine.py` - Gemini interactions, prompt generation, schema building
- `business_logic.py` - Notion queries, inventory hydration, handler dispatch
- `handlers.py` - Category-specific logic (places, youtube, movies, bookmarks, etc.)
- `notion_utils.py` - Property builders and Notion API operations
- `external_data.py` - URL extraction, web scraping, external API enrichment
- `gcp_secrets.py` - Secret Manager access with caching
- `clients.py` - Singleton client initialization (Gemini, Notion, Spotify, etc.)

## Infrastructure

- **Terraform** in `infrastructure/` manages GCP resources
- **GitHub Actions** deploys on push to `main` via Workload Identity Federation
- All secrets stored in GCP Secret Manager (27 total, see `config.yaml` for names)

## User Input Syntax

- `@` splits multiple items in one message
- `$` provides context (project name, date, status, category hint)
- Example: `Buy eggs $ groceries @ Update resume $ Career @ https://youtube.com/...`

## Deployment

Push to `main` triggers GitHub Actions which:
1. Generates `requirements.txt` from `uv.lock` per service
2. Copies `config.yaml` to service directories
3. Deploys to Cloud Run via `google-github-actions/deploy-cloudrun`

## Receptor - iOS & macOS Companion App

The `receptor/` folder contains a multi-platform SwiftUI app called **Receptor** with offline-first "Fire & Forget" architecture. Thoughts are queued locally in SwiftData and synced reliably via a cancel-and-restart flush model.

### Naming Conventions

- **Synapse** - The overall system/backend (this repo)
- **Receptor** - The iOS/macOS companion app that receives and forwards thoughts
- **Thought** - The data model representing captured text
- **Recept** - The verb for capturing and sending a thought to the processor (e.g., `receptThought()`)

The mental model: Receptor is a middleware app that "recepts" thoughts - it receives them from the user and sends them to the Synapse processor.

### Platform Support
- **iOS**: Full-featured app with background sync via BGTaskScheduler
- **macOS**: Menu bar app that stays running at login, syncs immediately when network changes

### Architecture
- SwiftUI + SwiftData for persistence in shared App Group container
- App Group (`group.com.alexmiller.receptor`) enables data sharing between main app and Shortcuts extensions
- `SyncManager` singleton handles network monitoring and queue processing
- `NWPathMonitor` triggers immediate sync when network is restored (works on both platforms)
- Strict FIFO ordering - sync stops on first failure to preserve order

### Sync Model: Cancel-and-Restart
Every trigger (shortcut, button press, network restore, app foreground) calls `requestFlush()`, which:
1. Cancels any in-progress flush task
2. Starts a new flush from the top of the queue (oldest first, FIFO)
3. Returns a `Task<Int, Never>` so callers can optionally `await .value` for the count

This ensures the most recent trigger always processes the most up-to-date queue. Between each thought upload, the flush checks `Task.isCancelled` to yield to newer flushes.

- `queueThought()` saves to DB then fires `requestFlush()` non-blocking (fire-and-forget)
- `receptThought()` is a blocking `URLSession.shared.data(for:)` call per thought
- Failed thoughts are retried on next flush (up to 25 retries before permanent abandonment)
- Thoughts are always persisted to SwiftData first, so they survive process death and sync on next trigger

### macOS-specific
- Menu bar app with quick capture popover (click brain icon)
- `LSUIElement = YES` hides dock icon when main window is closed
- Login item support via `SMAppService` - toggle in Settings
- No background task scheduler needed - app stays running and `NWPathMonitor` fires immediately

### App Intents (iOS/macOS Shortcuts integration)
- **Recept** (`CaptureThoughtIntent`) - Fire-and-forget, returns "Queued" instantly without blocking
- **Flush Thought Queue** (`FlushQueueIntent`) - Blocking intent that waits for sync completion, returns count of synced thoughts

### Sync Triggers
Thoughts track what triggered their sync via `sentVia` field:
- `captureIntent` - From Recept shortcut
- `flushIntent` - From Flush Thought Queue shortcut
- `appBecameActive` - App opened/foregrounded
- `networkRestored` - Connectivity restored after offline
- `backgroundTask` - iOS background processing
- `manualRetry` - User swiped to retry failed thought

### Notifications
- Only sent when sync is NOT triggered by `captureIntent` (to avoid double feedback)

### UI Structure
- Two tabs: Thoughts and Settings
- Thoughts tab: List with status badges, swipe-right to retry failed
- Settings tab: API configuration, queue statistics, debug sync log (+ Start at Login on macOS)
- Timestamps include seconds for debugging
- Online/Offline indicator in toolbar

### Code Organization (receptor/Receptor/)
- `Models/Thought.swift` - SwiftData model with ThoughtStatus enum
- `Services/SyncManager.swift` - Singleton: network monitoring, Background URLSession, queue processing (uses `receptThought()` to send)
- `Services/Configuration.swift` - App Group URLs, API key/URL storage
- `Services/AppDelegate.swift` - iOS-only: handles background URLSession events
- `Views/` - ThoughtsTab, SettingsTab, ThoughtListView, ComposeView, ContentView
- `Intents/` - CaptureThoughtIntent (Recept), FlushQueueIntent (Flush Thought Queue)
- `macOS/` - MenuBarView, LoginItemManager (macOS-specific)

### macOS Build & Run Commands

```bash
# Build for macOS
xcodebuild -project receptor/Receptor.xcodeproj -scheme Receptor \
  -destination "platform=macOS" -allowProvisioningUpdates build

# Run the built app
open ~/Library/Developer/Xcode/DerivedData/Receptor-*/Build/Products/Debug/Receptor.app

# Build and run in one command
xcodebuild -project receptor/Receptor.xcodeproj -scheme Receptor \
  -destination "platform=macOS" -allowProvisioningUpdates build && \
  open ~/Library/Developer/Xcode/DerivedData/Receptor-*/Build/Products/Debug/Receptor.app
```

### iOS Build & Run Commands

```bash
# List connected devices (find device ID)
xcrun xctrace list devices 2>&1 | grep -i iphone

# Build for physical device (replace device ID as needed)
xcodebuild -project receptor/Receptor.xcodeproj -scheme Receptor \
  -destination "id=00008140-000839E42111801C" \
  -allowProvisioningUpdates build

# Install to device (after building)
xcrun devicectl device install app --device 00008140-000839E42111801C \
  ~/Library/Developer/Xcode/DerivedData/Receptor-*/Build/Products/Debug-iphoneos/Receptor.app

# Build for simulator
xcodebuild -project receptor/Receptor.xcodeproj -scheme Receptor \
  -destination "platform=iOS Simulator,name=iPhone 16 Pro" build

# Run in simulator (after building)
xcrun simctl boot "iPhone 16 Pro"
xcrun simctl install booted ~/Library/Developer/Xcode/DerivedData/Receptor-*/Build/Products/Debug-iphonesimulator/Receptor.app
xcrun simctl launch booted com.alexmiller.receptor
```

### First-time device setup
1. Enable Developer Mode: Settings → Privacy & Security → Developer Mode
2. After first install, trust the developer certificate: Settings → General → VPN & Device Management → Developer App → Trust

**Bundle identifier:** `com.alexmiller.receptor`
