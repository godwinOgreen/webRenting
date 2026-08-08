

# Table of Contents

* [What Makes 0xRenting Different?](#what-makes-0xrenting-different)
* [Current Platform Status](#current-platform-status)
* [Project Maturity](#project-maturity)
* [Business Domains](#business-domains)
* [Engineering Artefacts](#engineering-artefacts)
* [Technology Stack](#technology-stack)
* [Repository Structure](#repository-structure)
* [System Architecture](#system-architecture)
* [Request Lifecycle](#request-lifecycle)
* [Development Workflow](#development-workflow)
* [Roadmap](#roadmap)
* [Contributing](#contributing)
* [License](#license)

---

# What Makes 0xRenting Different?

Most open-source rental platforms focus almost exclusively on delivering application features.

0xRenting takes a different approach.

The project treats software engineering as a complete discipline rather than simply writing application code.

Every major component of the platform is designed, documented and implemented with long-term maintainability in mind.

Rather than jumping directly into implementation, the project begins by understanding the business domain, defining architectural boundaries and documenting engineering decisions before writing code.

This repository is intended to demonstrate what a professionally engineered backend platform looks like throughout its entire lifecycle.

---

## The Engineering Mindset

Throughout the development of 0xRenting, every feature follows the same engineering process.

```text
Business Problem
        │
        ▼
Requirements
        │
        ▼
Domain Modelling
        │
        ▼
Architecture
        │
        ▼
Implementation
        │
        ▼
Testing
        │
        ▼
Deployment
        │
        ▼
Continuous Improvement
```

Instead of asking:

> "How do we build this feature?"

0xRenting asks:

> **"How should this feature be engineered so that it remains maintainable, secure and scalable?"**

That distinction influences every design decision across the platform.

---

# Current Platform Status

0xRenting is currently in active development.

The platform already provides a solid backend foundation with several core business domains implemented, while additional capabilities are planned as the project evolves.

The current focus is establishing a stable, well-architected platform before expanding into advanced marketplace features.

---

## Foundation Infrastructure

The shared infrastructure that supports every business domain has already been established.

| Component                |   Status   | Description                                                                  |
| ------------------------ | :--------: | ---------------------------------------------------------------------------- |
| Application Bootstrap    | ✅ Complete | FastAPI entry point, lifecycle management and global exception handling      |
| Configuration Management | ✅ Complete | Environment-driven configuration using centralised settings                  |
| Database Foundation      | ✅ Complete | SQLAlchemy session management, base model, mixins and reusable abstractions  |
| Redis Integration        | ✅ Complete | Shared Redis client prepared for caching and future distributed capabilities |
| Shared API Schemas       | ✅ Complete | Standardised success responses, pagination models and reusable API contracts |
| API Versioning           | ✅ Complete | Central API router with versioned endpoint aggregation                       |
| Authorisation Guards     | ✅ Complete | Shared permission framework for role-based access control                    |

---

## Implemented Business Domains

The following domains already contain working implementations and form the foundation of the platform.

| Domain                       |      Status     | Notes                                                                 |
| ---------------------------- | :-------------: | --------------------------------------------------------------------- |
| 🔐 Identity & Authentication | ✅ Core Complete | Registration, login, refresh tokens, logout, password reset and OAuth |
| 👤 User Management           | ✅ Core Complete | User profile management and agent functionality                       |
| 🏠 Property Marketplace      | ✅ Core Complete | Property CRUD operations and approval workflow                        |
| 📅 Booking Engine            | ✅ Core Complete | Viewing requests and appointment scheduling                           |
| 💳 Payments                  | ✅ Core Complete | Payment processing with Paystack integration                          |
| 💼 Subscriptions             | ✅ Core Complete | Subscription plans, billing logic and service layer                   |
| 💬 Messaging                 | ✅ Core Complete | Conversations, repositories, services and messaging APIs              |

---

## Domains Under Development

The following domains have either partial implementations or are planned as part of the platform roadmap.

| Domain                     |   Status   | Current Progress                                        |
| -------------------------- | :--------: | ------------------------------------------------------- |
| 🪪 KYC Verification        | 🟡 Partial | Data model available; service and API layer in progress |
| 🔔 Notification Engine     | 🟡 Planned | Event-driven notification architecture being designed   |
| ⭐ Reviews & Ratings        | 🟡 Planned | Tenant and property review system                       |
| 🔍 Search & Saved Searches | 🟡 Planned | Advanced filtering, search persistence and alerts       |
| 📊 Analytics               | 🟡 Planned | Platform metrics and business intelligence              |
| 🛡 Administration          | 🟡 Planned | Moderation, auditing and operational tooling            |
| 🖼 Media Management        | 🟡 Planned | Secure media upload, storage and optimisation           |
| 📄 Reports                 | 🟡 Planned | Reporting and operational exports                       |
| 🍪 Consent Management      | 🟡 Planned | Privacy preferences and consent tracking                |

---

# Project Maturity

The project follows an incremental development strategy.

Rather than implementing every feature at once, each business domain is completed to a production-quality standard before expanding the platform.

| Area            | Maturity            |
| --------------- | ------------------- |
| Infrastructure  | 🟢 Production Ready |
| Authentication  | 🟢 Production Ready |
| User Management | 🟢 Production Ready |
| Marketplace     | 🟢 Core Complete    |
| Booking Engine  | 🟢 Core Complete    |
| Payments        | 🟢 Core Complete    |
| Messaging       | 🟢 Core Complete    |
| Notifications   | 🟡 Planned          |
| Reviews         | 🟡 Planned          |
| Analytics       | 🟡 Planned          |
| Administration  | 🟡 Planned          |
| Media           | 🟡 Planned          |

This maturity model allows contributors to quickly understand the current state of the platform while providing transparency about future development priorities.

---

# Engineering Artefacts

One of the defining characteristics of 0xRenting is that engineering documentation is treated as a first-class deliverable.

In addition to the source code, the project includes a growing collection of design artefacts that document the reasoning behind the implementation.

These include:

* 🏛 System Architecture
* 🗄 Entity Relationship Diagrams (ERDs)
* 📘 Database Handbook
* 👥 User Journey Documentation
* 🔐 Role & Permission Matrix
* 🔄 Property State Machines
* ⚙ Automation & Event Flow Diagrams

These artefacts provide context that source code alone cannot communicate.

They explain not only **how** the platform works, but also **why** it was designed in a particular way.

---

## Engineering Insight

A production-ready platform is more than its implementation.

Architecture, documentation, operational thinking and clear engineering decisions are equally important.

0xRenting embraces this philosophy by ensuring that documentation evolves alongside the codebase, allowing contributors to understand the platform before modifying it.
