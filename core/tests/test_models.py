def test_submission_student_display_name(submission):
    # student_display_name should combine first and last name using submission.data
    data = submission.data or {}
    expected = " ".join(filter(None, [data.get("first_name"), data.get("last_name")])).strip()
    assert submission.student_display_name() == expected


def test_program_display_name_class_name(submission):
    # when class_name exists, program_display_name should return it
    data = submission.data or {}
    if "class_name" in data:
        assert submission.program_display_name() == data.get("class_name")
    else:
        # fallback: program_display_name should not crash and returns a string
        assert isinstance(submission.program_display_name(), str)
