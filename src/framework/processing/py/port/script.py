import fnmatch
import csv
import io
from datetime import datetime
from collections import namedtuple

import port.api.props as props
from port.api.commands import CommandSystemDonate, CommandUIRender

import pandas as pd
import zipfile

ExtractionResult = namedtuple("ExtractionResult", ["id", "title", "data_frame"])
filter_start_date = datetime(2017, 1, 1)


def get_in(dct, *key_path):
    for key in key_path:
        dct = dct.get(key)
        if dct is None:
            return
    return dct


def parse_csv_to_dataframe(records):
    data = []
    for record in records:
        timestamp = datetime.fromisoformat(
            record["com.samsung.health.step_count.create_time"]
        )

        if timestamp < filter_start_date:
            continue

        steps = int(record["com.samsung.health.step_count.count"])
        data.append([timestamp, steps])

    return pd.DataFrame(data, columns=["timestamp", "stepCount"])


def aggregate_steps_by_day(df):
    if df.empty:
        return pd.DataFrame(columns=["date", "stepCount"])
    df["date"] = df["timestamp"].dt.strftime("%Y-%m-%d")
    aggregated_df = df.groupby(["date"])["stepCount"].sum().reset_index()
    return aggregated_df


def extract(df):
    df = aggregate_steps_by_day(df)
    df = df.rename(columns={"date": "Datum", "stepCount": "Aantal"})
    return [
        ExtractionResult(
            id="step_count",
            title=props.Translatable({"en": "Daily steps", "nl": "Stappen per dag"}),
            data_frame=df,
        )
    ]


def process(sessionId):
    yield donate(f"{sessionId}-tracking", '[{ "message": "user entered script" }]')

    meta_data = []
    meta_data.append(("debug", f"start"))

    # STEP 1: select the file
    data = None
    while True:
        promptFile = prompt_file()
        fileResult = yield render_donation_page(promptFile, 33)
        if fileResult.__type__ == "PayloadString":
            meta_data.append(("debug", f"extracting file"))
            extractionResult = extract_data_from_zip(fileResult.value)
            if extractionResult == "invalid":
                meta_data.append(
                    ("debug", f"prompt confirmation to retry file selection")
                )
                retry_result = yield render_donation_page(retry_confirmation(), 33)
                if retry_result.__type__ == "PayloadTrue":
                    meta_data.append(("debug", f"skip due to invalid file"))
                    continue
                else:
                    meta_data.append(("debug", f"retry prompt file"))
                    break
            if extractionResult == "no-data":
                retry_result = yield render_donation_page(
                    retry_no_data_confirmation(), 33
                )
                if retry_result.__type__ == "PayloadTrue":
                    continue
                else:
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

    # STEP 2: ask for consent
    if data is not None:
        meta_data.append(("debug", f"prompt consent"))
        prompt = prompt_consent(data, meta_data)
        consent_result = yield render_donation_page(prompt, 67)
        if consent_result.__type__ == "PayloadJSON":
            meta_data.append(("debug", f"donate consent data"))
            yield donate(f"{sessionId}", consent_result.value)

    yield render_end_page()


def render_end_page():
    page = props.PropsUIPageEnd()
    return CommandUIRender(page)


def render_donation_page(body, progress):
    header = props.PropsUIHeader(
        props.Translatable({"en": "Samsung Health", "nl": "Samsung Health"})
    )

    footer = props.PropsUIFooter(progress)
    page = props.PropsUIPageDonation("sumsung-health", header, body, footer)
    return CommandUIRender(page)


def retry_confirmation():
    text = props.Translatable(
        {
            "en": f"Unfortunately we cannot process your file. Continue, if you are sure that you selected the right file. Try again to select a different file.",
            "nl": f"Helaas kunnen we uw bestand niet verwerken. Weet u zeker dat u het juiste bestand heeft gekozen? Ga dan verder. Probeer opnieuw als u een ander bestand wilt kiezen.",
        }
    )
    ok = props.Translatable({"en": "Try again", "nl": "Probeer opnieuw"})
    cancel = props.Translatable({"en": "Continue", "nl": "Verder"})
    return props.PropsUIPromptConfirm(text, ok, cancel)


def retry_no_data_confirmation():
    text = props.Translatable(
        {
            "en": f"Unfortunately we could not detect any information about the number of steps in your file. Continue, if you are sure that you selected the right file. Try again to select a different file.",
            "nl": f"We hebben helaas geen informatie over het aantal genomen stappen in uw bestand gevonden. Weet u zeker dat u het juiste bestand heeft gekozen? Ga dan verder. Probeer opnieuw als u een ander bestand wilt kiezen.",
        }
    )
    ok = props.Translatable({"en": "Try again", "nl": "Probeer opnieuw"})
    cancel = props.Translatable({"en": "Continue", "nl": "Verder"})
    return props.PropsUIPromptConfirm(text, ok, cancel)


def prompt_file():
    description = props.Translatable(
        {
            "en": f"Click 'Choose file' to choose the file that you received from Samsung. If you click 'Continue', the data that is required for research is extracted from your file.",
            "nl": f"Klik op ‘Kies bestand’ om het bestand dat u ontvangen hebt van Samsung te kiezen. Als u op 'Verder' klikt worden de gegevens die nodig zijn voor het onderzoek uit uw bestand gehaald.",
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


def filter_files(file_list):
    pattern = "*com.samsung.shealth.tracker.pedometer_step_count.*.csv"
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
