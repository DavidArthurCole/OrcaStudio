#include <catch2/catch_all.hpp>

#include "../../shared/slicer_linux_runtime_core/RuntimeCoreJson.hpp"

namespace JsonRuntime = Slic3r::SlicerLinuxRuntime::JsonRuntime;

TEST_CASE("PrintParams queue_plate_id survives the runtime JSON round trip", "[RuntimeCoreJson]") {
    BBL::PrintParams p{};
    p.dev_id = "dev-1";
    p.queue_plate_id = "plate-42";
    const nlohmann::json j = JsonRuntime::to_json(p);
    REQUIRE(j.at("queue_plate_id") == "plate-42");
    const BBL::PrintParams back = JsonRuntime::print_params_from_json(j);
    REQUIRE(back.queue_plate_id == "plate-42");
    REQUIRE(back.dev_id == "dev-1");
}

TEST_CASE("PrintParams JSON without queue_plate_id loads an empty queue plate", "[RuntimeCoreJson]") {
    const nlohmann::json j = {{"dev_id", "dev-1"}};
    const BBL::PrintParams back = JsonRuntime::print_params_from_json(j);
    REQUIRE(back.queue_plate_id.empty());
    REQUIRE(back.dev_id == "dev-1");
}
