import fnmatch
import csv
import io
from datetime import datetime, timedelta
from collections import namedtuple

import port.api.props as props
from port.api.commands import CommandSystemDonate, CommandUIRender

import pandas as pd
import zipfile
import os

ExtractionResult = namedtuple("ExtractionResult", ["id", "title", "data_frame"])
filter_start_date = datetime(2017, 1, 1)
unix_epoch = datetime(1970, 1, 1)


def get_in(dct, *key_path):
    for key in key_path:
        dct = dct.get(key)
        if dct is None:
            return
    return dct


def parse_csv_to_dataframe(records):
    data = []
    for record in records:
        unix_time = int(record["day_time"]) / 1000
        timestamp = unix_epoch + timedelta(days=unix_time / 86400)

        if timestamp < filter_start_date:
            continue

        device = record["deviceuuid"]
        steps = int(record["count"])
        data.append([device, timestamp, steps])

    return pd.DataFrame(data, columns=["device", "timestamp", "stepCount"])


def aggregate_steps_by_day(df):
    if df.empty:
        return pd.DataFrame(columns=["date", "device", "stepCount"])
    df["date"] = df["timestamp"].dt.strftime("%Y-%m-%d")
    aggregated_df = df.groupby(["date", "device"])["stepCount"].sum().reset_index()
    return aggregated_df


def extract(df):
    df = aggregate_steps_by_day(df)
    df = df.rename(
        columns={"date": "Datum", "stepCount": "Aantal", "device": "Apparaat ID"}
    )
    return [
        ExtractionResult(
            id="step_count",
            title=props.Translatable({"en": "Daily steps", "nl": "Stappen per dag"}),
            data_frame=df,
        )
    ]


def process(sessionId):
    key = "zip-contents-example"
    meta_data = []
    meta_data.append(("debug", f"{key}: start"))

    meta_data = []
    meta_data.append(("debug", f"start"))

    # STEP 1: select the file
    data = None
    while True:
        promptFile = prompt_file()
        fileResult = yield render_donation_page(promptFile)
        if fileResult.__type__ == "PayloadString":
            meta_data.append(("debug", f"extracting file"))
            extractionResult = extract_data_from_zip(fileResult.value)
            if extractionResult == "invalid":
                meta_data.append(
                    ("debug", f"prompt confirmation to retry file selection")
                )
                retry_result = yield render_donation_page(retry_confirmation())
                if retry_result.__type__ == "PayloadTrue":
                    meta_data.append(("debug", f"skip due to invalid file"))
                    continue
                else:
                    meta_data.append(("debug", f"retry prompt file"))
                    break
            if extractionResult == "no-data":
                retry_result = yield render_donation_page(retry_no_data_confirmation())
                if retry_result.__type__ == "PayloadTrue":
                    continue
                data = ("aborted", fileResult.value)
                break
            else:
                meta_data.append(
                    ("debug", f"extraction successful, go to consent form")
                )
                data = extractionResult
                break
        else:
            meta_data.append(("debug", f"skip to next step"))
            break

    if isinstance(data, list):
        prompt = prompt_consent(data, meta_data)
    else:
        prompt = prompt_report_consent(os.path.basename(data[1]), meta_data)

    meta_data.append(("debug", f"prompt consent"))
    consent_result = yield render_donation_page(prompt)
    if consent_result.__type__ == "PayloadJSON":
        meta_data.append(("debug", f"donate consent data"))
        yield donate(f"{sessionId}", consent_result.value)
    if consent_result.__type__ == "PayloadFalse":
        value = '{"status" : "donation declined"}'
        yield donate(f"{sessionId}", value)


def render_end_page():
    page = props.PropsUIPageEnd()
    return CommandUIRender(page)


def render_donation_page(body):
    header = props.PropsUIHeader(
        props.Translatable({"en": "Samsung health", "nl": "Samsung health"})
    )

    page = props.PropsUIPageDonation("sumsung-health", header, body)
    return CommandUIRender(page)


def retry_confirmation():
    text = props.Translatable(
        {
            "en": f"Unfortunately, we cannot process your file. Continue, if you are sure that you selected the right file. Try again to select a different file.",
            "nl": f"Helaas, kunnen we uw bestand niet verwerken. Weet u zeker dat u het juiste bestand heeft gekozen? Ga dan verder. Probeer opnieuw als u een ander bestand wilt kiezen.",
        }
    )
    ok = props.Translatable({"en": "Try again", "nl": "Probeer opnieuw"})
    cancel = props.Translatable({"en": "Continue", "nl": "Verder"})
    return props.PropsUIPromptConfirm(text, ok, cancel)


def retry_no_data_confirmation():
    text = props.Translatable(
        {
            "en": f"There does not seem to be any information in your file. That can happen. You will still be able to receive your reward. Click 'Continue' and proceed. If you have selected the wrong file by accident, then click 'Try again'.",
            "nl": f"Er lijkt geen informatie in uw pakketje te zitten. Dat kan gebeuren. Met een leeg pakketje komt u nog steeds in aanmerking voor de vergoeding. Druk op 'Verder' om door te gaan. Als u per ongeluk het verkeerde bestand hebt gekozen, druk dan op 'Probeer opnieuw'.",
        }
    )
    ok = props.Translatable({"en": "Try again", "nl": "Probeer opnieuw"})
    cancel = props.Translatable({"en": "Continue", "nl": "Verder"})
    return props.PropsUIPromptConfirm(text, ok, cancel)


def prompt_file():
    description = props.Translatable(
        {
            "en": f"Click 'Choose file' to choose the file that you received from Samsung. If you click 'Continue', the data that is required for research is extracted from your file. This may take some time. Thank you for your patience.",
            "nl": f"Klik op ‘Kies bestand’ om het bestand dat u ontvangen hebt van Samsung te kiezen. Als u op 'Verder' klikt worden de gegevens die nodig zijn voor het onderzoek uit uw bestand gehaald. Dit kan soms even duren. Een moment geduld a.u.b.",
        }
    )

    return props.PropsUIPromptFileInput(description, "application/zip")


def prompt_consent(tables, meta_data):
    log_title = props.Translatable({"en": "Log messages", "nl": "Log berichten"})

    tables = [
        props.PropsUIPromptConsentFormTable(table.id, table.title, table.data_frame)
        for table in tables
    ]
    meta_frame = pd.DataFrame(meta_data, columns=["type", "message"])
    meta_table = props.PropsUIPromptConsentFormTable(
        "log_messages", log_title, meta_frame
    )
    return props.PropsUIPromptConsentForm(tables, [meta_table])


def prompt_report_consent(filename, meta_data):
    log_title = props.Translatable({"en": "Log messages", "nl": "Log berichten"})

    tables = [
        props.PropsUIPromptConsentFormTable(
            "filename",
            props.Translatable({"nl": "Bestandsnaam", "en": "Filename"}),
            pd.DataFrame({"Bestandsnaam": [filename]}),
        )
    ]

    meta_frame = pd.DataFrame(meta_data, columns=["type", "message"])
    meta_table = props.PropsUIPromptConsentFormTable(
        "log_messages", log_title, meta_frame
    )
    return props.PropsUIPromptConsentForm(
        tables,
        [meta_table],
        description=props.Translatable(
            {
                "nl": "Hieronder ziet u de bestandsnaam van het gekozen bestand. U kunt deze doneren, zodat de onderzoekers kunnen zien of u een leeg pakketje hebt of dat er iets mis is gegaan.",
                "en": "Below you can view the name of the chosen file. You can donate this file name, so that the researchers can view whether you had an empty package or something went wrong.",
            }
        ),
        donate_question=props.Translatable(
            {
                "en": "Do you want to donate the above file name?",
                "nl": "Wilt u de bovenstaande bestandsnaam doneren?",
            }
        ),
        donate_button=props.Translatable({"nl": "Ja, doneer", "en": "Yes, donate"}),
    )


def filter_files(file_list):
    pattern = "*com.samsung.shealth.step_daily_trend.*.csv"
    return [f for f in file_list if fnmatch.fnmatch(f, pattern)]


def load_and_process_file(z, file, callback):
    with z.open(file, "r") as f:
        f.seek(0)
        ft = io.TextIOWrapper(f, encoding="utf8")
        ft.readline()
        return callback(csv.DictReader(ft))


def extract_data_from_zip(zip_filepath):
    with zipfile.ZipFile(zip_filepath, "r") as z:
        files = filter_files(z.namelist())
        dfs = [load_and_process_file(z, f, parse_csv_to_dataframe) for f in files]
    if not dfs:
        return "no-data"
    df = pd.concat(dfs, ignore_index=True)
    return extract(df)


def donate(key, json_string):
    return CommandSystemDonate(key, json_string)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        print(extract_data_from_zip(sys.argv[1]))
    else:
        print("please provide a zip file as argument")
