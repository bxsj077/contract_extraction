from contract_extraction_v2.ocr.engine import RapidOcrEngine


class FakeOutput:
    boxes = [[[0, 0], [100, 0], [100, 20], [0, 20]]]
    txts = ["合同编号：A001"]
    scores = [0.98]


class FakeRapidOcr:
    def __call__(self, image):
        return FakeOutput()


def test_new_rapidocr_output_is_adapted():
    engine = RapidOcrEngine(FakeRapidOcr())
    lines = engine.recognize("page.png", dpi=300, preprocessing="normal", ocr_pass="normal")
    assert lines[0].text == "合同编号：A001"
    assert lines[0].score == 0.98
    assert lines[0].dpi == 300
    assert lines[0].model == "PP-OCRv6"
