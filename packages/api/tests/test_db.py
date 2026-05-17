from resona_api.db.models import Job


def test_job_has_profile_fields():
    job = Job(filename="f.wav")
    assert hasattr(job, "profile")
    assert hasattr(job, "profile_config")
    assert hasattr(job, "structured")


def test_replacement_table_removed():
    import resona_api.db.models as m
    assert not hasattr(m, "Replacement")
    assert not hasattr(m, "InitialPrompt")
