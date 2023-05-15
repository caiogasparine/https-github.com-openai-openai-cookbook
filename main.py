# imports needed to run the code in this notebook
import ast
import openai  # used for calling the OpenAI API
from get_functions import import_all_modules, import_modules_from_file
import re
import subprocess
import inspect
from typing import Any, Tuple, List  # type: ignore


def chat_gpt_wrapper(
    input_messages=[],
    engine: str = "GPT4",
    model="gpt-4",
    max_tokens: int = 1000,
    temperature: float = 0.2,
    reruns_if_fail: int = 0,
) -> Tuple[Any, List]:
    """Outputs a unit test for a given Python function, using a 3-step GPT-3 prompt."""

    # the init function for this classs is\
    # {init_str}\

    plan_response = openai.ChatCompletion.create(
        engine=engine,
        model=model,
        messages=input_messages,
        max_tokens=max_tokens,
        temperature=temperature,
        stream=False,
    )
    unit_test_completion = plan_response.choices[0].message.content
    input_messages.append({"role": "assistant", "content": plan_response.choices[0].message.content})
    # check the output for errors
    code_output = extract_all_python_code(unit_test_completion)
    try:
        ast.parse(code_output)
    except SyntaxError as e:
        print(f"Syntax error in generated code: {e}")
        if reruns_if_fail > 0:
            print("Rerunning...")
            return chat_gpt_wrapper(
                input_messages=input_messages,
                engine=engine,
                max_tokens=max_tokens,
                temperature=temperature,
                reruns_if_fail=reruns_if_fail - 1,  # decrement rerun counter when calling again
            )
    return code_output, input_messages


def extract_all_python_code(string):
    start_marker = "```python"
    end_marker = "```"
    pattern = re.compile(f"{start_marker}(.*?)({end_marker})", re.DOTALL)
    matches = pattern.findall(string)
    if not matches:
        return None
    return "".join([match[0].strip() for match in matches])


def get_modified_code_files() -> dict:
    """Get a dictionary of all the code files that are added/ modified/ deleted

    Returns:
        dict: dictionary of all the code files that are added/ modified/ deleted along with its status
    """

    # Get the list of changed files
    output = subprocess.check_output(["git", "diff", "--name-status", "@{upstream}..HEAD"])

    # Filter for only code files
    allowed_extensions = [".py", ".cpp", ".java", ".js", ".scala", ".sas", ".json", ".csv"]
    code_files = [f for f in output.decode().split("\n") if os.path.splitext(f)[1] in allowed_extensions]

    # Get the type of change for each code file
    changes = {}
    for file in code_files:
        print(file)
        try:
            status, filename = file.split("\t")
            print(f"Status: {status}, Filename: {filename}")
            changes[filename] = status
        except ValueError:
            print(f"Skipping invalid line: {file}. Most likely it is due to renaming the file.")

    return changes


def generate_unit_test(
    directory: str,
    repo_explanation: str,  # explanation of the project repo - got from documentation.
    unit_test_package: str = "pytest",
    doc_package: str = "sphinx",
    platform: str = "Python 3.9",
    engine: str = "GPT4",
    model="gpt-4",
    max_tokens: int = 1000,
    temperature: float = 0.4,
):
    """pipeline to generate unit test for all code files that contain modules in a directory

    Args:
        directory (str): path to the entire repo folder
        repo_explanation (str): high level explanation of what the project does and what needs to have unit test generated
            1. Indicate which code files/ classes/ functions need to generate unit test
            2. List all dependencies (custom attribute values that OpenAI cannot generate randomly) needed to the run the codes (
                if there's any config file, testing path (S3), schema of the dataframe, clearly state in this explanation as well)
            For example, in the DQ check project, repo explanation is as below:
                - This project is run mainly by file `dq_utility.py` file. This file is packaged to be used as library
                - This `dq_utility.py` includes `DataCheck` class along with its methods to check the data quality of a dataframe.
                It will generate a csv output of the lines with errors in a csv file
                - Some needed info for dependencies are as below (class attributes):
                    * rules files (csv) that can provide the schema (columns) of DF - config_path (S3 path where hold the config csv file in this case)
                    * name of the file to have data quality check (proper file name included in the config csv rules file) - file_name attribute
                    * the source (vendor) of the file to have data quality check - src_system attribute
        unit_test_package (str): package/ library used for unit testing, i.e. `pytest`, `unittest`, etc. Defaults to "pytest"
        platform (str, optional): code language/version or platforms that need to generate unit test. Defaults to "Python 3.9".
        engine (str, optional): OpenAI engine used to generate unit tests. Defaults to "GPT4".
        model (str, optional): OpenAI model used to generate unit tests. Defaults to "gpt-4".
        max_tokens (int, optional): maximum tokens used by OpenAI API. temperature = 0 can sometimes get stuck in repetitive loops. Defaults to 1000.
        temperature (float, optional): temperature setting used by OpenAI API. Defaults to 0.4.
    """
    object_dict = {}
    directory_dict = {}
    object_dict, directory_dict = import_all_modules(
        directory=directory, object_dict=object_dict, directory_dict=directory_dict
    )

    i = 0
    messages = []
    doc_prompt = f"""you are providing documentation and typing (type hints) of code to be used in {doc_package}, as an example for the function
    ```python
    def read_s3_file(self, file_path):
        file_res = urlparse(file_path)
        try:
            file_obj = s3_resource.Object(file_res.netloc, file_res.path.lstrip("/"))
            return file_obj.get()["Body"].read()
        except ClientError:
            raise FileNotFoundError(f"File cannot be found in S3 given path file_path"):
    ```
    this is a proper response:
    ```python
    def read_s3_file(self, file_path:str)->str:
    \"\"\"
    Reads the contents of a file from an S3 bucket.
    :param file_path: The S3 URI of the file to read, e.g. "s3://my-bucket/my-file.txt".
    :type file_path: str
    :return: The contents of the file as a byte string.
    :rtype: bytes
    :raises FileNotFoundError: If the file cannot be found in the specified S3 bucket.
    :Example:
    (some examples)
    \"\"\"
    file_res = urlparse(file_path)
    try:
        file_obj = s3_resource.Object(file_res.netloc, file_res.path.lstrip("/"))
        return file_obj.get()["Body"].read()
    except ClientError:
        raise FileNotFoundError(f"File cannot be found in S3 given path file_path")
    ```
    """
    full_prompt = f"you are providing unit test using {unit_test_package}` and {platform}\
        {repo_explanation}. to give details about the structure of the repo look at the dictionary below, it includes all files\
        and if python, all function and classes, if json first and second level keys and if csv, the column names :{directory_dict},"
    messages.append({"role": "system", "content": full_prompt})
    doc_messages = []
    doc_messages.append({"role": "system", "content": doc_prompt})
    for file_path, objects in object_dict.items():
        generated_unit_test = ""
        if "objects" in objects:
            for obj_key, obj_value in objects["objects"].items():
                if obj_value:
                    if type(obj_value) == dict:
                        for class_method_name, class_method in obj_value.items():
                            class_name = str(class_method).split(".")[0].split(" ")[1]
                            function_name = str(class_method).split(".")[1].split(" ")[0]
                            function_to_test = inspect.getsource(class_method)
                            doc_messages.append(
                                {
                                    "role": "user",
                                    "content": f"provide documentation for {function_to_test} in class {class_name} that can be used in {doc_package}",
                                }
                            )
                            doc_code, doc_messages = chat_gpt_wrapper(input_messages=doc_messages)
                            if len(doc_messages) > 4:
                                doc_messages = doc_messages[:1] + doc_messages[-4:]
                            # Open the file in read mode and read its content
                            with open(file_path, "r") as file:
                                file_content = file.read()
                            if doc_code.startswith("class"):
                                doc_code = "\n".join(doc_code.split("\n")[1:])
                            else:
                                doc_code = "    " + doc_code.replace("\n", "\n    ")
                            # Replace a specific string in the file content
                            new_content = file_content.replace(function_to_test, doc_code)

                            # Open the file in write mode and write the new content to it
                            with open(file_path, "w") as file:
                                file.write(new_content)

                            request_for_unit_test = f" provide unit test for the function `{function_name} in {class_name}`.\
                                ```python\
                                {function_to_test} \
                                ```\
                                and the path of the function is {file_path}\
                                "

                            messages.append({"role": "user", "content": request_for_unit_test})
                            unit_test_code, messages = chat_gpt_wrapper(input_messages=messages)
                            if len(doc_messages) > 4:
                                doc_code = doc_code[:1] + doc_code[-4:]
                            generated_unit_test += f"{function_name} in class {class_name}`\n{unit_test_code}"
                            current_test_path = f"test_code/unit_test/test_{class_name}_{function_name}.py"
                            with open(current_test_path, "w") as test_f:
                                test_f.write(unit_test_code)
                    else:
                        function_name = obj_key
                        function_to_test = inspect.getsource(obj_value)
                        prompt_to_explain_the_function = f" provide unit test for the function `{function_name}`.\
                            ```python\
                            {function_to_test} \
                            ```\
                            and the path of the function is {file_path}\
                            "
                        messages.append({"role": "user", "content": prompt_to_explain_the_function})
                        unit_test_code, messages = chat_gpt_wrapper(
                            input_messages=messages,
                            engine=engine,
                            model=model,
                            max_tokens=max_tokens,
                            temperature=temperature,
                        )
                        generated_unit_test += f"{function_name}`\n{unit_test_code}"
                        current_test_path = f"test_code/unit_test/test_{class_name}_{function_name}.py"
                        with open(current_test_path, "w") as test_f:
                            test_f.write(unit_test_code)
            unit_test_path = file_path[::-1].replace("\\", "/test_", 1)[::-1]
            with open(unit_test_path, "w") as test_f:
                test_f.write(generated_unit_test)
                # MAX_RETRIES = 3
                # retry_count = 0
                # fixed = False
                # i += 1
                # if i > 3:
                #     break


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv

    load_dotenv()
    import os
    import openai

    openai.api_type = "azure"
    openai.api_base = "https://aoaihackathon.openai.azure.com/"
    openai.api_version = "2023-03-15-preview"
    openai.api_key = os.getenv("OPENAI_API_KEY")
    engine = "GPT4"
    model = "gpt-4"

    # generate_unit_test(
    #     directory="test_code",
    #     repo_explanation="This repo uses the dq_utility to check the data quality based on different sources given in\
    #         csv files like az_ca_pcoe_dq_rules_innomar, it will generate a csv output of the lines with errors in a csv file, to use the repo u can:\
    #         from dq_utility import DataCheck\
    #         from pyspark.sql import SparkSession\
    #         spark = SparkSession.builder.getOrCreate()\
    #         df=spark.read.parquet('test_data.parquet')\
    #         Datacheck(source_df=df,\
    #         spark_context= spark,\
    #         config_path=s3://config-path-for-chat-gpt-unit-test/config.json,\
    #         file_name=az_ca_pcoe_dq_rules_innomar.csv,\
    #         src_system=bioscript)",
    #     )

    modified_code_files = get_modified_code_files()

    print(modified_code_files)

    if len(modified_code_files) != 0:
        for file_path, status in modified_code_files.items():
            file_extension = os.path.splitext(file_path)[1]
            unit_test_path = file_path[::-1].replace("\\", "/test_", 1)[::-1]
            if status == "D":
                # delete unit test files
                if os.path.exists(unit_test_path):
                    os.remove(unit_test_path)
            else:
                # object_dict = {}
                # directory_dict = {}
                # object_dict, directory_dict = import_modules_from_file(
                #     filepath=file_path, extension=file_extension, object_dict=object_dict, directory_dict=directory_dict
                # )
                # use git diff to get the content of the file from the previous remote commit
                git_diff_output = subprocess.check_output(['git', 'diff', '@{upstream}..HEAD', file_path])
                # decode the bytes to a string
                git_diff_output = git_diff_output.decode('utf-8')
                # split the output into lines
                git_diff_lines = git_diff_output.split('\n')

                # loop through the diff output to find the lines that start with either '+' or '-'
                for line in git_diff_lines:
                    if line.startswith('+ def') or line.startswith("- def"):
                        print(line)
