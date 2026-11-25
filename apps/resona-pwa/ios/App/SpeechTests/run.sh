#!/bin/sh
# Typechecks every file of the iOS ResonaSpeech plugin that does not need Capacitor or UIKit
# (ios/App/App/Speech/) against the macOS SDK, then compiles the pure helpers with
# main.swift and runs the checks. Needs only the Command Line Tools (swiftc, macOS SDK with
# the Speech framework of macOS 26 or newer), no Xcode. ResonaSpeechPlugin.swift and
# ResonaBridgeViewController.swift import Capacitor/UIKit and are compiled by Xcode only.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
speech="$here/../App/Speech"
target="$(uname -m)-apple-macosx14.0"
swiftc -typecheck -swift-version 5 -target "$target" "$speech"/*.swift
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
swiftc -swift-version 5 -target "$target" -o "$out/speech-tests" \
	"$here/main.swift" \
	"$speech/SpeechTranscript.swift" \
	"$speech/PcmFrames.swift" \
	"$speech/RecognitionRestarts.swift" \
	"$speech/LanguageSupport.swift" \
	"$speech/PcmResampler.swift"
"$out/speech-tests"
