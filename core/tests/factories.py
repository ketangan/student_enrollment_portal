import factory
from factory import django
from faker import Faker

fake = Faker()


class SchoolFactory(django.DjangoModelFactory):
    class Meta:
        model = "core.School"

    slug = factory.Sequence(lambda n: f"{fake.slug()}-{n}")
    display_name = factory.LazyAttribute(lambda o: f"{fake.company()} {o.slug}")
    website_url = factory.LazyAttribute(lambda o: f"https://{fake.domain_name()}")
    source_url = ""


class UserFactory(django.DjangoModelFactory):
    class Meta:
        model = "auth.User"
        skip_postgeneration_save = True

    username = factory.Sequence(lambda n: f"{fake.user_name()}{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@{fake.free_email_domain()}")

    @factory.post_generation
    def password(obj, create, extracted, **kwargs):
        # set a usable password
        obj.set_password(extracted or "password")
        if create:
            obj.save()


class SchoolAdminMembershipFactory(django.DjangoModelFactory):
    class Meta:
        model = "core.SchoolAdminMembership"
        skip_postgeneration_save = True

    user = factory.SubFactory(UserFactory)
    school = factory.SubFactory(SchoolFactory)

    @factory.post_generation
    def make_staff(obj, create, extracted, **kwargs):
        # ensure user is staff
        obj.user.is_staff = True
        if create:
            obj.user.save()


class SubmissionFactory(django.DjangoModelFactory):
    class Meta:
        model = "core.Submission"

    school = factory.SubFactory(SchoolFactory)
    data = factory.LazyFunction(lambda: {
        "first_name": fake.first_name(),
        "last_name": fake.last_name(),
        "class_name": fake.word().title() + " Class",
        "dance_style": fake.word(ext_word_list=["ballet", "jazz", "hiphop", "contemporary"]) ,
        "skill_level": fake.random_element(elements=("beginner", "intermediate", "advanced")),
    })
