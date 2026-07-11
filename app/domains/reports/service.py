# report_service.py

async def create_report(
    self, db: AsyncSession, reporter_id: uuid.UUID,
    target_type: str, target_id: uuid.UUID,
    reason: str, details: Optional[str],
) -> Report:
    # ── Check report count ──
    count_stmt = select(func.count()).where(
        Report.reporter_id == reporter_id,
        Report.target_type == target_type,
        Report.target_id == target_id,
    )
    count = (await db.execute(count_stmt)).scalar()

    if count >= MAX_REPORTS_PER_ENTITY:    # 3
        raise BusinessRuleException(
            f"You have already reported this {target_type} the maximum number of times."
        )

    # ── Check cooldown ──
    if count > 0:
        last_report_stmt = select(Report.created_at).where(
            Report.reporter_id == reporter_id,
            Report.target_type == target_type,
            Report.target_id == target_id,
        ).order_by(Report.created_at.desc()).limit(1)

        last_report_at = (await db.execute(last_report_stmt)).scalar_one()

        hours_since_last = (datetime.now(tz=timezone.utc) - last_report_at).total_seconds() / 3600

        if hours_since_last < REPORT_COOLDOWN_HOURS:    # 24
            raise BusinessRuleException(
                f"Please wait {REPORT_COOLDOWN_HOURS} hours before reporting "
                f"this {target_type} again."
            )

    # ── Create report ──
    report = Report(
        reporter_id=reporter_id,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        details=details,
    )
    db.add(report)
    await db.commit()
    return report
