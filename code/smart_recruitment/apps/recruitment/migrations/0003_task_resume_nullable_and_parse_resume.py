# Generated for parse_resume flow: allow RecruitmentTask without a Resume yet.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("recruitment", "0002_positiontemplate"),
    ]

    operations = [
        migrations.AlterField(
            model_name="recruitmenttask",
            name="resume",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.PROTECT,
                verbose_name="简历",
                to="recruitment.resume",
            ),
        ),
    ]