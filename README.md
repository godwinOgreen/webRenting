<div align="center">

# 0xRenting

### **Engineering Rental Platforms the Right Way.**

#### *An open-source reference implementation for building modern property rental platforms using production-ready backend architecture and cloud-native engineering principles.*

> **Repository:** `webRenting`
> **Project Name:** **0xRenting**

---

> 🚧 **Status:** Active Development

---

<!-- Replace this with the official banner later -->

<p align="center">
<img src="assets/banner/github-banner.svg" alt="0xRenting Banner" width="100%">
</p>

![Python](https://img.shields.io/badge/Python-3.13-blue?style=for-the-badge\&logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-Modern_API-009688?style=for-the-badge\&logo=fastapi)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Database-336791?style=for-the-badge\&logo=postgresql)
![Redis](https://img.shields.io/badge/Redis-Cache-DC382D?style=for-the-badge\&logo=redis)

![Docker](https://img.shields.io/badge/Docker-Containers-2496ED?style=for-the-badge\&logo=docker)
![Google%20Cloud](https://img.shields.io/badge/Google_Cloud-Cloud_Native-4285F4?style=for-the-badge\&logo=googlecloud)
![NGINX](https://img.shields.io/badge/NGINX-Reverse_Proxy-009639?style=for-the-badge\&logo=nginx)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-ORM-red?style=for-the-badge)

</div>

---

# Welcome

Most tutorials teach you **how to build features**.

Very few teach you **how to engineer systems**.

0xRenting is an open-source backend platform that demonstrates how production-grade rental marketplaces can be designed, documented, implemented and deployed using modern software engineering principles.

Rather than focusing exclusively on CRUD operations, the project documents the complete engineering journey behind building a scalable property rental platform—from business domain modelling and database design to cloud infrastructure, deployment strategy and operational thinking.

The goal is simple:

> **Teach developers how experienced engineering teams build rental platforms that can evolve, scale and be maintained over time.**

---

# Why 0xRenting?

Modern rental platforms are significantly more complex than displaying property listings.

They require identity management, role-based permissions, booking workflows, messaging, subscriptions, payments, moderation, notifications, analytics and operational tooling—all working together within a maintainable architecture.

Many educational projects demonstrate these features individually.

Few explain how they fit together as a cohesive system.

0xRenting exists to bridge that gap.

The repository combines implementation with architecture, documentation and engineering decisions so contributors understand not only **what** was built, but also **why** it was built that way.

---

# What Makes This Project Different?

This repository treats documentation as a first-class engineering deliverable.

Alongside the source code, you'll find artefacts that are commonly produced during the design of production software, including:

* 🏛 System Architecture
* 🗄 Entity Relationship Diagrams (ERDs)
* 📘 Database Handbook
* 👥 User Journey Documentation
* 🔄 State Machines
* 🔐 Role & Permission Matrix
* ⚙ Automation & Event Flows
* ☁ Cloud Deployment Strategy
* 📚 Engineering Decision Records

The objective is to provide a complete reference implementation rather than a collection of isolated examples.

---

# Current Status

0xRenting is under active development.

The platform already includes a stable backend foundation together with several core business domains.

## Core Infrastructure

* ✅ FastAPI application bootstrap
* ✅ Global exception handling
* ✅ Configuration management
* ✅ SQLAlchemy integration
* ✅ Redis integration
* ✅ Shared response schemas
* ✅ API versioning
* ✅ Role-based permission guards

## Implemented Domains

* ✅ Identity & Authentication
* ✅ User Management
* ✅ Property Marketplace
* ✅ Booking Engine
* ✅ Payments
* ✅ Subscriptions
* ✅ Messaging

## Planned Domains

* 🟡 KYC Verification
* 🟡 Notifications
* 🟡 Reviews
* 🟡 Search
* 🟡 Saved Searches
* 🟡 Analytics
* 🟡 Administration
* 🟡 Media Management
* 🟡 Reports
* 🟡 Consent Management

---

# Technology Stack

| Layer            | Technologies           |
| ---------------- | ---------------------- |
| Backend          | FastAPI, Python        |
| Database         | PostgreSQL, SQLAlchemy |
| Cache            | Redis                  |
| Authentication   | JWT, OAuth             |
| Payments         | Paystack               |
| Reverse Proxy    | NGINX                  |
| Containerisation | Docker                 |
| Cloud            | Google Cloud Platform  |
| API              | RESTful APIs           |
| Version Control  | Git & GitHub           |

---

# Documentation

The repository documentation is organised as an engineering handbook.

| Document                           | Description                                    |
| ---------------------------------- | ---------------------------------------------- |
| `docs/00-project-standards.md`     | Engineering standards and project philosophy   |
| `docs/01-getting-started.md`       | Introduction to the project                    |
| `docs/02-architecture.md`          | High-level architecture and design principles  |
| `docs/03-business-domains.md`      | Business capabilities and domain boundaries    |
| `docs/04-database-handbook.md`     | Database design and modelling                  |
| `docs/05-api-design.md`            | API conventions and design standards           |
| `docs/06-security.md`              | Authentication, authorisation and security     |
| `docs/07-deployment.md`            | Local, Docker and cloud deployment             |
| `docs/08-infrastructure.md`        | Infrastructure components and operations       |
| `docs/09-observability.md`         | Monitoring, logging and operational visibility |
| `docs/10-engineering-decisions.md` | Architectural Decision Records (ADRs)          |
| `docs/11-roadmap.md`               | Planned milestones and future development      |
| `docs/12-contributing.md`          | Contribution guidelines                        |

---

# Quick Start

```bash
git clone https://github.com/godwinOgreen/webRenting.git

cd webRenting
```

Further setup instructions are available in the Getting Started guide.

---

# Repository Structure

```text
webRenting/

├── README.md
├── docs/
├── assets/
├── app/
├── tests/
├── alembic/
├── docker/
├── nginx/
├── scripts/
└── ...
```

---

# Project Roadmap

The project is being developed incrementally.

The current roadmap focuses on completing the remaining business domains before expanding into advanced marketplace capabilities.

Upcoming milestones include:

* Notification Engine
* KYC Verification
* Reviews
* Search Platform
* Saved Searches
* Analytics
* Administration
* Media Management
* Reporting
* Production Deployment

A detailed roadmap is maintained in:

```text
docs/11-roadmap.md
```

---

# Contributing

Contributions are welcome.

Before contributing, please read:

* Project Standards
* Architecture Guide
* Contribution Guide

Following the documented engineering standards helps keep the project consistent and maintainable.

---

# License

This project will be released under the MIT License.

---

<div align="center">

### Engineering Rental Platforms the Right Way.

**Design • Engineer • Deploy • Scale**

</div>
