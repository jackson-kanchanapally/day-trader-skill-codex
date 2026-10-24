import React from "react";

const people = [
  { skin: "#d7a17d", hair: "#3a2924", shirt: "#8fa9c2" },
  { skin: "#a96c4d", hair: "#201b1a", shirt: "#b29fc9" },
  { skin: "#edc4a4", hair: "#55403b", shirt: "#a8b7a0" },
  { skin: "#bc8768", hair: "#272326", shirt: "#c7ad88" },
  { skin: "#e0b495", hair: "#33292b", shirt: "#bd969b" },
];

export function deskState(phase) {
  if (phase === "running") return "working";
  if (phase === "completed") return "report";
  if (phase === "failed" || phase === "invalid") return "attention";
  return "sleeping";
}

export default function AgentDesk({ phase, index = 0, verdict }) {
  const state = deskState(phase);
  const person = people[index % people.length];
  const seal =
    verdict === "BLOCK"
      ? "#ee9d90"
      : verdict === "PASS"
        ? "#96d4ad"
        : "#e7c58e";
  const sleeping = state === "sleeping";
  const report = state === "report";
  return (
    <svg
      className={`desk-scene desk-${state}`}
      viewBox="0 0 240 180"
      role="img"
      aria-label={
        report
          ? "Agent sitting at a desk holding a finished report"
          : state === "working"
            ? "Agent working at a laptop"
            : state === "attention"
              ? "Agent at a desk awaiting attention"
              : "Agent sleeping at a desk, waiting for confirmed activity"
      }
    >
      <ellipse cx="122" cy="163" rx="91" ry="9" fill="#050505" />
      <path d="M28 155H218M43 165H201" stroke="#272727" strokeWidth="1" />
      <path
        d="M187 103V66M179 66H196L202 82H173Z"
        fill="#424242"
        stroke="#686868"
        strokeWidth="1.5"
      />
      <path
        d="M178 107H198"
        stroke="#626262"
        strokeWidth="4"
        strokeLinecap="round"
      />
      {state === "working" && (
        <path d="M176 84L157 116H211L198 84" fill="#e8debc" opacity=".07" />
      )}
      <rect
        x="58"
        y="78"
        width="45"
        height="61"
        rx="15"
        fill="#303030"
        stroke="#484848"
        strokeWidth="2"
      />
      <path
        d="M80 132V157M65 159H96"
        stroke="#535353"
        strokeWidth="4"
        strokeLinecap="round"
      />
      <path
        d="M78 121L87 145H108M100 122L112 143H129"
        fill="none"
        stroke="#4d5260"
        strokeWidth="10"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M102 148H117M124 146H136"
        stroke="#b1b1b1"
        strokeWidth="5"
        strokeLinecap="round"
      />
      <path
        d="M65 115V94Q65 80 82 80H95Q110 81 113 98L111 123H67Z"
        fill={person.shirt}
      />
      <g
        className="person-head"
        transform={sleeping ? "rotate(18 90 75)" : undefined}
      >
        <rect x="83" y="75" width="12" height="13" rx="4" fill={person.skin} />
        {index % 2 === 1 && (
          <path
            d="M72 57Q69 31 91 32Q117 34 111 80L101 85L77 75Z"
            fill={person.hair}
          />
        )}
        <rect x="74" y="41" width="32" height="39" rx="15" fill={person.skin} />
        <circle cx="74" cy="62" r="4" fill={person.skin} />
        <circle cx="106" cy="62" r="4" fill={person.skin} />
        <path
          d="M73 56V47Q75 31 93 34Q109 35 108 52L99 49L95 43Q88 54 73 56Z"
          fill={person.hair}
        />
        {sleeping ? (
          <path
            d="M81 61Q84 64 87 61M95 61Q98 64 101 61"
            fill="none"
            stroke="#47332c"
            strokeWidth="1.7"
            strokeLinecap="round"
          />
        ) : (
          <>
            <circle cx="84" cy="61" r="1.7" fill="#2c2523" />
            <circle cx="98" cy="61" r="1.7" fill="#2c2523" />
            {index % 3 === 2 && (
              <g fill="none" stroke="#343434" strokeWidth="1.3">
                <rect x="78" y="56" width="11" height="9" rx="3" />
                <rect x="93" y="56" width="11" height="9" rx="3" />
                <path d="M89 60H93" />
              </g>
            )}
          </>
        )}
        <path
          d={report ? "M86 70Q91 75 96 70" : "M87 70H94"}
          stroke="#78503e"
          strokeWidth="1.5"
          fill="none"
          strokeLinecap="round"
        />
      </g>
      <path
        d="M29 120L48 112H209L221 120L212 127H39Z"
        fill="#505050"
        stroke="#686868"
        strokeWidth="1"
      />
      <path
        d="M39 126V158M209 126V158"
        stroke="#656565"
        strokeWidth="5"
        strokeLinecap="round"
      />
      <path d="M41 128H208" stroke="#303030" strokeWidth="4" />
      <rect
        x="128"
        y="84"
        width="47"
        height="31"
        rx="3"
        fill="#202020"
        stroke="#727272"
        strokeWidth="2"
      />
      <rect
        x="132"
        y="88"
        width="39"
        height="23"
        rx="1"
        fill={state === "working" ? "#25342e" : "#292929"}
      />
      {state === "working" && (
        <g
          className="screen-code"
          fill="none"
          stroke="#b5d3c1"
          strokeWidth="1.5"
        >
          <path d="M137 94H150M137 99H164M137 104H156" />
          <path className="screen-cursor" d="M160 104H165" />
        </g>
      )}
      {report && (
        <path
          d="M143 99L149 104L160 94"
          fill="none"
          stroke="#9dab9e"
          strokeWidth="2"
        />
      )}
      <path d="M121 118L128 113H175L182 118Z" fill="#8a8a8a" />
      <path
        d="M125 119H180"
        stroke="#b0b0b0"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      <rect x="49" y="103" width="12" height="14" rx="3" fill="#a19b8d" />
      <path
        d="M61 106Q70 105 67 111Q65 114 61 112"
        fill="none"
        stroke="#a19b8d"
        strokeWidth="2"
      />
      {state === "working" && (
        <path
          className="coffee-steam"
          d="M54 98Q50 94 55 90"
          fill="none"
          stroke="#a19b8d"
          strokeWidth="1.3"
        />
      )}
      {report ? (
        <g className="held-report">
          <path
            d="M70 96L79 107L101 101M107 96L120 105L132 97"
            fill="none"
            stroke={person.skin}
            strokeWidth="8"
            strokeLinecap="round"
          />
          <path
            d="M91 81H119L130 92V115H91Z"
            fill="#eeeae0"
            stroke="#c5c1b8"
            strokeWidth="1.2"
          />
          <path d="M119 81V92H130" fill="#d0ccc2" />
          <path
            d="M98 92H113M98 98H123M98 103H114"
            stroke="#8c8b83"
            strokeWidth="1.7"
            strokeLinecap="round"
          />
          <circle cx="121" cy="108" r="4" fill={seal} />
          <circle cx="93" cy="104" r="4" fill={person.skin} />
          <circle cx="129" cy="102" r="4" fill={person.skin} />
        </g>
      ) : sleeping ? (
        <g>
          <path
            d="M74 96L91 111H116M108 96L114 109H127"
            fill="none"
            stroke={person.skin}
            strokeWidth="8"
            strokeLinecap="round"
          />
          <g
            className="sleep-letters"
            fill="#b2b2c2"
            fontFamily="system-ui"
            fontWeight="600"
          >
            <text x="119" y="62" fontSize="12">
              z
            </text>
            <text x="135" y="47" fontSize="16">
              z
            </text>
            <text x="154" y="29" fontSize="21">
              Z
            </text>
          </g>
        </g>
      ) : (
        <g className="typing-arms">
          <path
            d="M73 96L84 109L127 116M108 96L115 109L143 115"
            fill="none"
            stroke={person.skin}
            strokeWidth="8"
            strokeLinecap="round"
          />
        </g>
      )}
      {state === "attention" && (
        <g>
          <circle cx="185" cy="49" r="13" fill="#352622" stroke="#ae776c" />
          <text
            x="185"
            y="54"
            textAnchor="middle"
            fill="#ee9d90"
            fontSize="16"
            fontWeight="700"
          >
            !
          </text>
        </g>
      )}
    </svg>
  );
}
